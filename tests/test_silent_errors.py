"""P12-AB：静默吞异常不许再增加（体检：全仓 197 处，runtime/ 50 处）。

`except: pass` 是这套系统最容易藏事故的写法：P12-U 的"状态分叉"就因为一句话被吞掉
而在日志里藏了两个小时。这一轮把最要紧的几处补上了日志（记忆/上一段动作没存上、
配置备份失败、租约回收失败、昵称补不上、JSON 迁移失败），这里用**棘轮**钉住：
runtime/ 里的静默 handler 只能减少，不能增加。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SILENT = re.compile(r"except[^\n]*:\s*\n\s+(pass|return\b|continue\b)")
# 2026-09-27 体检后（补了 6 处日志）：runtime/ 45 处。
# 想加新的静默 handler？请先想清楚"失败了谁会知道"；确实无害就把这个数 +1 并写明理由。
RUNTIME_SILENT_MAX = 45


def _silent_count(mod: Path) -> int:
    return len(SILENT.findall(mod.read_text(encoding="utf-8")))


def test_runtime_silent_handlers_do_not_grow():
    total = sum(_silent_count(p) for p in (ROOT / "runtime").glob("*.py"))
    assert total <= RUNTIME_SILENT_MAX, (
        "runtime/ 静默吞异常变多了（{} > {}）：新增的几处请至少写一行日志，"
        "或者把 RUNTIME_SILENT_MAX 调高并说明为什么失败无所谓".format(
            total, RUNTIME_SILENT_MAX))


def test_key_losses_are_logged(monkeypatch):
    """这五处"丢了用户会察觉"的失败必须留下日志。"""
    import runtime.group_state as gs
    import runtime.identity as ident
    import runtime.config_store as cs

    logged = []
    monkeypatch.setattr("runtime.obs.log", lambda m: logged.append(m), raising=False)

    # 1) 记忆没存上
    monkeypatch.setattr(gs.state_store, "group_set",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk full")))
    gs._WARNED.clear()
    gs._remember("c1", "记住我", "某人")
    assert any("记忆" in m for m in logged), logged

    # 2) 上一段动作没存上（会导致 /继续 说"没有上一段"）
    logged.clear()
    gs._set_last_action("c1", {"type": "insult"})
    assert any("上一段动作" in m for m in logged), logged

    # 3) 昵称批量加载失败
    logged.clear()
    ident._WARNED.clear()
    monkeypatch.setattr(ident, "get_members",
                        lambda cid: (_ for _ in ()).throw(RuntimeError("http 500")))
    ident._load_member_names("c1")
    assert any("加载成员昵称" in m for m in logged), logged

    # 4) 配置备份失败
    logged.clear()
    cs._WARNED_BACKUP.clear()
    cs._warn_backup("permission denied")
    assert any("备份失败" in m for m in logged), logged
