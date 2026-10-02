# -*- coding: utf-8 -*-
"""P12-Y：把发骚/调教/注册这几处**硬编码提示语**换成话术取值。

屋主 2026-09-27 要"工作群里若瑾的详细使用说明"，核对时发现这些提示语
（调教进行中、命令为空、续写失败、注册成功…）全是小小糯的软萌口吻，
落到纯 S 的若瑾身上会串人设；注册那几句还写着"糯糯姐"（**名字都是错的**）。

做法：统一走 `_ph(cid, key, 兜底句)`；人设话术表里给 nuo（逐字等于原句）、
ruojin（S 口吻）、rose_butler（管家口吻）各一份。默认人设那份逐字不变，
所以认证群行为不动。

用法：python tools/p12y_voice_pass.py [--check]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BOT = ROOT / "bot.py"

# (原文, 替换成, 期望出现次数)
PAIRS = [
    ('return "还没有发骚内容可以继续哦～先 /发骚 试试～"',
     'return _ph(cid, "cont_no_content", "还没有发骚内容可以继续哦～先 /发骚 试试～")', 1),
    ('return "还没有上一段可以继续哦～先 /发骚 或 /羞辱 某人试试～"',
     'return _ph(cid, "cont_no_action", "还没有上一段可以继续哦～先 /发骚 或 /羞辱 某人试试～")', 1),
    ('return "上一段不是可以继续的内容哦～"',
     'return _ph(cid, "cont_bad_type", "上一段不是可以继续的内容哦～")', 1),
    ('return "续写失败啦，换个指令试试～"',
     'return _ph(cid, "cont_fail", "续写失败啦，换个指令试试～")', 3),
    ('return "现在没有进行中的发骚哦～先 /发骚 试试～"',
     'return _ph(cid, "rewrite_idle", "现在没有进行中的发骚哦～先 /发骚 试试～")', 1),
    ('return "还没有发骚内容可以重写哦～先 /发骚 试试～"',
     'return _ph(cid, "rewrite_no_content", "还没有发骚内容可以重写哦～先 /发骚 试试～")', 1),
    ('return "重写失败啦，再试一次～"',
     'return _ph(cid, "rewrite_fail", "重写失败啦，再试一次～")', 1),
    ('return "🎲 发骚进行中哦～暂无法响应其他指令，想继续发 /继续，想结束发 /结束～"',
     'return _ph(cid, "play_busy_fasao", "🎲 发骚进行中哦～暂无法响应其他指令，'
     '想继续发 /继续，想结束发 /结束～")', 1),
    ('return "🔗 调教进行中哦～暂无法响应其他指令，想参与发 /命令 xxx，想结束发 /调教结束～"',
     'return _ph(cid, "play_busy_diaojiao", "🔗 调教进行中哦～暂无法响应其他指令，'
     '想参与发 /命令 xxx，想结束发 /调教结束～")', 1),
    ('reply = "🔗 调教结束啦～"',
     'reply = _ph(cid, "dj_end", "🔗 调教结束啦～")', 1),
    ('reply = "命令不能为空哦～格式 /命令 xxx"',
     'reply = _ph(cid, "dj_cmd_empty", "命令不能为空哦～格式 /命令 xxx")', 1),
    ('reply = "调教进行中哦～先 /调教结束 再发起新的～"',
     'reply = _ph(cid, "dj_busy", "调教进行中哦～先 /调教结束 再发起新的～")', 1),
    ('reply = "🔗 调教进行中哦～想参与就发 /命令 xxx，或 /调教结束 收尾～"',
     'reply = _ph(cid, "dj_block", "🔗 调教进行中哦～想参与就发 /命令 xxx，'
     '或 /调教结束 收尾～")', 1),
    ('return "只有糯糯姐才能帮别人注册哦～你自己发 /注册 就可以啦～"',
     'return _ph(_active_cid(), "reg_self_only", '
     '"只有糯糯姐才能帮别人注册哦～你自己发 /注册 就可以啦～")', 1),
    ('return "没找到这个人呢，注册不了～"',
     'return _ph(_active_cid(), "reg_not_found", "没找到这个人呢，注册不了～")', 1),
    ('return "✅ " + full + " 已经注册过啦～不用重复注册哦，发 /查 " + full + " 就能看档案啦～"',
     'return _ph(_active_cid(), "reg_exists",\n'
     '                   "✅ " + full + " 已经注册过啦～不用重复注册哦，"\n'
     '                   "发 /查 " + full + " 就能看档案啦～", who=full)', 1),
    ('return "✅ " + full + " 注册成功啦～已经记进档案，以后查" + full + "会更丰富哦～"',
     'return _ph(_active_cid(), "reg_ok",\n'
     '                   "✅ " + full + " 注册成功啦～已经记进档案，"\n'
     '                   "以后查" + full + "会更丰富哦～", who=full)', 1),
    ('return "注册失败，存档出了点问题～"',
     'return _ph(_active_cid(), "reg_fail", "注册失败，存档出了点问题～")', 1),
    ('await sender.send("🤖 收到同步指令～正在拉取增量消息，稍等一下下哦～", cid)',
     'await sender.send(_ph(cid, "sync_working",\n'
     '                                              "🤖 收到同步指令～正在拉取增量消息，'
     '稍等一下下哦～"), cid)', 1),
]

HELPER = '''

def _ph(cid: str, key: str, fallback: str = "", **kw) -> str:
    """取本群人设的固定话术；没配就退回代码里的兜底句。

    P12-Y：发骚/调教/注册这些**提示语**原来硬编码在流程里，全是小小糯的软萌口吻
    （"…哦～""…啦～"），注册那几句甚至写着"糯糯姐"——落到若瑾身上名字和口吻都不对。
    统一走这里之后，人设话术表能一句话换掉（页面可改、热加载 1 秒生效）。
    """
    try:
        return p(key, cid, **kw) or fallback
    except Exception:                                          # noqa: BLE001
        return fallback


def register_user(query: str, sender_uid: str) -> str:'''


def main() -> None:
    src = BOT.read_text(encoding="utf-8")
    check = "--check" in sys.argv
    bad = []
    for old, new, want in PAIRS:
        got = src.count(old)
        if got != want:
            bad.append("{} 处（期望 {}）：{}".format(got, want, old[:40]))
    if bad:
        print("❌ 位置对不上，一个字都没改：")
        for b in bad:
            print("   " + b)
        raise SystemExit(1)
    if check:
        print("✅ 全部 {} 条都还在原位（未修改）".format(len(PAIRS)))
        return
    for old, new, _ in PAIRS:
        src = src.replace(old, new)
    if "def _ph(cid: str, key: str, fallback" not in src:
        anchor = '\ndef register_user(query: str, sender_uid: str) -> str:'
        assert src.count(anchor) == 1, "找不到 register_user 定义"
        src = src.replace(anchor, HELPER)
    BOT.write_text(src, encoding="utf-8")
    print("✅ 已替换 {} 条，并插入 _ph() 助手".format(len(PAIRS)))


if __name__ == "__main__":
    main()
