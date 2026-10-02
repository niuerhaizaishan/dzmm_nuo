"""P12-Y：提示语按人设走 —— 逐条核对三个群的**实际生效值**。

屋主 2026-09-27 要"工作群里若瑾的详细使用说明"，核对时发现发骚/调教/注册这些
提示语全是小小糯的软萌口吻（注册那几句还写着"糯糯姐"）。这里把三份口径并排打出来。
"""
import sys

sys.path.insert(0, ".")
import personas as P                                          # noqa: E402

AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"

P.set_config_personas({RUOJIN: "ruojin"})

KEYS = ("cont_no_content", "cont_no_action", "cont_bad_type", "cont_fail",
        "rewrite_idle", "rewrite_no_content", "rewrite_fail",
        "play_busy_fasao", "play_busy_diaojiao", "dj_end", "dj_cmd_empty",
        "dj_busy", "dj_block", "reg_self_only", "reg_not_found",
        "reg_exists", "reg_ok", "reg_fail", "sync_working")

CUTE = ("哦～", "啦", "呀", "♡")

for key in KEYS:
    a = P.p(key, AUTH, who="某人")
    r = P.p(key, RUOJIN, who="某人")
    b = P.p(key, ROSE, who="某人")
    flag = ""
    if not r:
        flag += "  ❌若瑾缺这条"
    if r == a:
        flag += "  ❌与大群同款（没换口吻）"
    if any(c in r for c in CUTE):
        flag += "  ⚠️若瑾版还有软萌口吻"
    if any(c in b for c in CUTE):
        flag += "  ⚠️玫瑰房版还有软萌口吻"
    print("{}".format(key))
    print("   认证群 : {}".format(a))
    print("   工作群 : {}".format(r))
    print("   玫瑰房 : {}".format(b))
    if flag:
        print("   " + flag)

print()
print("残留检查：这些句子是不是只作为 `_ph(...)` 的兜底字面量存在（属正常）")
src = open("bot.py", encoding="utf-8").read()
lines = src.splitlines()
left = [s for s in ("只有糯糯姐才能帮别人注册", "还没有发骚内容可以继续哦",
                    "调教进行中哦～先 /调教结束", "命令不能为空哦",
                    "现在没有进行中的发骚哦", "重写失败啦")
        if any(s in ln and "_ph(" not in ln for ln in lines)]
print("  还在流程里直接返回的：", left or "无（都走话术了）")
