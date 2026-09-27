"""P12-AB：出站队列失败要**看得见**（体检发现 2026-09-26 一条星象卡静默丢了）。

背景：`OutboxSender` 发失败就 `mark_failed` 并留一条 `failed` 记录，按设计
**不自动重发**（未 ACK 不重发，避免重复打扰）。于是它永远躺在表里，
除了当时日志里一个 `❌` 之外没有任何东西会再提它。现在：
  · 主循环每 30 分钟打印一行 `📤 出站队列：pending/x failed/y；最近失败 …`；
  · 健康巡检盯"失败条数**增长**"（而不是"有失败"，否则永久刷屏）。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import check_bot_health as health                              # noqa: E402
import bot                                                     # noqa: E402
from runtime.outbox import FAILED, OutboxSender, OutboxStore, SENT  # noqa: E402


class _Inner:
    """最小可用的假 sender：第一次失败，之后成功。"""

    def __init__(self):
        self.calls = 0
        self.last_failure = "ambiguous"

    async def send(self, text, cid, allow_fallback=True):
        self.calls += 1
        return self.calls > 1


def test_sender_exposes_queue_observability(dbdir):
    """发送器要能回答"失败了几条、最近那条是什么"。"""
    st = OutboxStore(dbdir / "o.db")
    inner = _Inner()

    class _NoOutbox(OutboxSender):
        pass

    import asyncio

    async def run():
        s = OutboxSender(inner, st, dry=False)
        await s.send("第一条会失败", "cid-1")
        await s.send("第二条成功", "cid-1")
        return s

    s = asyncio.run(run())
    assert s.failed_count() == 1
    assert s.pending_count() == 0
    last = s.recent_failed(1)[0]
    assert last["text"] == "第一条会失败"
    assert last["status"] == FAILED
    assert "sender 返回 False" in last["last_error"]
    st.close()


def test_status_line_reports_a_lost_message(dbdir, monkeypatch):
    """状态行必须把它写出来（含群、字数、正文片段、失败原因）。"""
    st = OutboxStore(dbdir / "o.db")
    st.enqueue(chatroom_id="4a56be71-aaaa", text="🌹 今日星象 · 双子座" + "长" * 50)
    oid = st.recent(1)[0]["id"]
    st.mark_failed(oid, "sender 返回 False [ambiguous]")

    lines = []
    monkeypatch.setattr(bot, "log", lambda msg: lines.append(msg))
    bot._log_outbox_status(st)
    assert lines and lines[0].startswith("📤 出站队列")
    assert "failed=1" in lines[0]
    assert "4a56be71" in lines[0]
    assert "今日星象" in lines[0]
    st.close()


def test_health_check_alerts_on_new_failures_only(dbdir, monkeypatch):
    """巡检：失败**增长**才告警；没有新增就安静（否则永久刷屏）。"""
    monkeypatch.setattr(health, "ROOT", dbdir)
    (dbdir / "data").mkdir(exist_ok=True)
    db = dbdir / "data" / "outbox.db"
    st = OutboxStore(db)
    oid = st.enqueue(chatroom_id="cid-1", text="会失败的一条")
    st.mark_failed(oid, "sender 返回 False")
    st.close()

    ob = health.check_outbox()
    assert ob == {"pending": 0, "failed": 1, "last": ob["last"]}
    assert ob["last"]["text"] == "会失败的一条"

    # 再跑一次（失败数没变）→ 不产生新告警：这里只验证计数可比较
    ob2 = health.check_outbox()
    assert ob2["failed"] == ob["failed"]


def test_health_check_outbox_reads_readonly(dbdir, monkeypatch):
    """巡检必须用只读连接（绝不锁生产库），且库不存在时返回 None 而不是炸。"""
    monkeypatch.setattr(health, "ROOT", dbdir)
    assert health.check_outbox() is None            # 还没有 data/outbox.db
    src = (ROOT / "check_bot_health.py").read_text(encoding="utf-8")
    assert "mode=ro" in src, "必须是只读连接"
    assert "OUTBOX_PENDING_MAX" in src and "outbox_pending" in src, "积压也要告警"
