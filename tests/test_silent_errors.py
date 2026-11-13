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
# 2026-10-01 P3-3-1（bot.py 入群链路搬入 runtime/joins.py）：45 → 49。
# 新增的 4 处全是 bot.py **原样**搬来的既定静默，不是新写的：
#   · add_pending_screen —— 待筛持久化尽力而为，撞码期间打日志反而刷屏；
#   · notify_join_delayed —— 告警本身失败不得破坏筛查主流程
#     （send_weixin→feishu_alert 内层已在 08:24 修复中记 stderr，这是第二道网）；
#   · kick_and_notify ×2 —— 踢人失败本体有 log()，静默的只是"移出通知/失败通知"
#     这两层飞书转发，不该让通知失败把踢人流程打断。
# 2026-10-01 P3-3-2（bot.py 斜杠分派链搬入 runtime/dispatch.py）：49 → 54。
# 新增的 5 处同样是 bot.py **原样**搬来的既定静默，不是新写的：
#   · _extract_xp —— XP 标签解析不出来就当没有（注册档案的锦上添花项）；
#   · _live_insult_text —— 实时发言拉不到就只用档案背景（羞辱素材有降级路径）；
#   · _record_insult —— 羞辱历史记不上不影响本次输出（只是下次角度避让差一点）；
#   · continue_last —— 续写生成本身失败会回一句"续写失败啦"（话术即用户可见的日志）；
#   · summarize_chat —— 时间戳解析不了的聊天行跳过（老消息格式不一，属正常过滤）。
# 2026-10-01 P3-3-3（bot.py 房间消息块搬入 runtime/room.py）：54 → 55。
# 新增的 1 处同样是 bot.py **原样**搬来的既定静默，不是新写的：
#   · _room_male —— 查到实时性别后"顺手写回档案"失败就放弃（下次再查即可，
#     不影响本次性别判定，更不打断房间流程）。
#   · P3-7（+10）—— Sender 类随 runtime/sender.py 纳入扫描：token 抄回/刷新、
#     页面重载、Socket.IO 长消息重试、读回验证这些"机会主义"路径本就自带
#     一行日志或明确返回 False，失败都不影响发送主链（搬家前不在 runtime 视野内）。
#   · P3-8（+1）—— _maybe_devil_cold_roast（runtime/roast.py）：生成失败返回空串
#     并写回 30 分钟重试标记 —— 静默的是"这轮不吐"，不是错误状态。
#   · P3-9（+1）—— _judge_pending（runtime/climax.py）：AI 评定失败按空结果处理
#     （heat 不动、写回判定时间），下个 2 小时周期重试。
#   · P3-13（+7）—— 档案同步族整族搬入 runtime/profile_sync.py，全是 bot.py
#     **原样**搬来的既定静默，不是新写的：
#     scan_load return {} / scan_save pass —— 扫描基线 JSON 读坏/写失败属
#       "尽力而为"，丢了基线最多下次重新建（重新建 = 首日静默，无害）；
#     daily_profile_scan get_members continue —— 单群成员表拉不到就跳过该群，
#       其余群照扫（打日志反而每群刷一条）；
#     _to_local return sent_at[:16] —— 时间戳解析不了就原样返回（老消息格式不一）；
#     run_sync_profiles pass ×2 —— 单个成员刷 bio 失败/单个档案总结生成失败，
#       都只影响那一个人，且发言记录已在前面落库（数据不丢）；
#     run_greet_once 读回验证 pass —— 读回失败当"已发出"处理，不补发
#       （误判漏发 → 群里收到两遍午安，2026-08-24 实测过）。
# 想加新的静默 handler？请先想清楚"失败了谁会知道"；确实无害就把这个数 +1 并写明理由。
#   · P3-15（+1）—— other_bot_uids（runtime/turns.py）：bot_uids() 读不到就跳过
#     该来源（后面还有平台 isBot / 昵称兜底两层），失败也绝不冒出去。
#   · P3-18（+1）—— calc_message_lag（runtime/reply_gate.py）：单条消息时间戳
#     解析不了就跳过（老消息格式不一，属正常过滤）。
#   · P3-19（+2）—— 主循环体搬入 runtime/mainloop.py：两处"尽力而为"的既定静默
#     随体搬迁（cookie 恢复标记重置/出站状态读不到就跳过本轮自证）。
#   · 2026-11-13（+5）—— 语音房与摸鱼币两批新模块的回退分支，都是"失败就用安全默认值，
#     绝不让业务报错"的形状，且都另有日志/上层提示：
#       · runtime/office_wallet.py（+4）：余额/扣币/发币调不通时返回 {"ok": False}，
#         调用方（office.py）会把原因回给玩家，不会静默吞掉。
#       · runtime/gallery_download.py 等下载/批处理模块（+1 起）：单条失败跳过，
#         不影响整批，失败条数由汇总日志给出。
RUNTIME_SILENT_MAX = 83


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
