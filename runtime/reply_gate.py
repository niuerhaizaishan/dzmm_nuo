"""回复判定（P3-18 从 bot.py 拆出）。

四个小判定器，主循环用它决定"这条消息要不要回、以什么形式回"：
  · quotes_self —— 这条消息是不是"右键回复了我说过的那条"（_ref_of 在 at_chat）；
  · quote_reply_on —— "引用她的消息算不算触发"：`quote_reply` 开关 **或**
    本群开了 `at_only`（P12-BM B：at_only 的语义就是"只接对她说的话"，
    引用她的消息明显是对她说的 ⇒ 天然等价于 @，茶水间 09-30 就是这么静默不应的）；
  · find_reply_command —— 上下文最近 3 条里有没有没处理的 `/` 指令；
  · calc_message_lag —— 最新一条群友消息从平台发出到现在隔了多少秒（拿不到 -1）。
    平台时间是 UTC，容器是本地时间（UTC+8）—— P12-I 用来分清"平台/推送慢"
    还是"我们慢"的关键数字。

**接缝注入**：quotes_self 的 is_self（test_quote_reply patch `bot.is_self`）、
quote_reply_on 的 _feat_on（闸门唯一入口，⚠️ 参数必须沿用 `_feat_on` 名 ——
静态扫描只认 `_feat_on(` 字面量调用）；message_lag/find_reply_command 无测试
patch，is_self 直连 identity。
"""
from datetime import datetime, timedelta
import time

from runtime.at_chat import _ref_of
from runtime.identity import is_self


__all__ = ["quotes_self", "quote_reply_on", "find_reply_command", "calc_message_lag"]


def quotes_self(m: dict, *, is_self) -> bool:
    """这条消息是不是"右键回复了我说过的那条"。"""
    ref = _ref_of(m)
    return bool(ref.get("sent_by")) and is_self(ref["sent_by"])


def quote_reply_on(cid: str, *, _feat_on) -> bool:
    """"引用她的消息算不算触发" —— `quote_reply` 开关 **或** 本群开了 `at_only`。

    P12-BM B（2026-09-30）：`at_only` 的语义就是"只接对她说的话"，而**引用她的消息**
    明显是对她说的 ⇒ 天然等价于 @，不该再要求每群单独开 `quote_reply`。
    否则每开一个新群都会再踩一次"引用她没反应"的坑 —— 茶水间 09-30 就是这么静默不应的
    （诊断见 `docs/P12-BM-茶水间引用触发-方案.md`）。

    非 `at_only` 群仍然由 `quote_reply` 说了算（玫瑰房那套"谁能触发管家"的房间策略
    不受影响：它既没开 at_only、也没开 quote_reply）。
    """
    return bool(_feat_on(cid, "quote_reply")) or bool(_feat_on(cid, "at_only"))


def find_reply_command(context: list[dict]) -> str:
    last_three = [m["text"] for m in context[-3:]]
    for t in last_three:
        t = t.strip()
        if t.startswith("/"):
            return t
    return ""


def calc_message_lag(msgs) -> float:
    """最新一条**群友**消息从平台发出到现在隔了多少秒（拿不到就 -1）。

    平台时间是 UTC，容器是本地时间（UTC+8）—— 这是 P12-I 用来分清
    "平台/推送慢" 还是 "我们慢" 的关键数字：日志里的时间戳只能看到
    "我们开始处理之后" 的耗时，看不到消息在路上等了多久。
    """
    newest = None
    for m in msgs or ():
        try:
            if is_self(m.get("sent_by")):
                continue
            sa = str(m.get("sent_at") or "")
            if len(sa) < 19:
                continue
            t = datetime.strptime(sa[:19], "%Y-%m-%d %H:%M:%S") \
                + timedelta(hours=8)
        except Exception:                                      # noqa: BLE001
            continue
        if newest is None or t > newest:
            newest = t
    if newest is None:
        return -1.0
    return time.time() - time.mktime(newest.timetuple())
