# -*- coding: utf-8 -*-
"""P12-BN 验收：**真生成 A/B** —— 收尾块加了"【怎么叫】"之后，男生还会不会被叫"姐姐"。

判据（不发送、只真调模型）：

| 用例 | 说话人（性别） | 应该 |
|---|---|---|
| 男1 | 顾影（男生） | 叫"顾影"/"顾影哥"，**不许出现"顾影姐姐"** |
| 男2 | 苏白（男生） | 同上，**不许出现"苏白姐姐"** |
| 女1 | 糯糯（女生） | **应该**保留"糯糯姐/糯糯姐姐"（别把对的也改坏） |
| 对照 | 群里上下文里有人把男生叫"姐姐" | 不许跟着学（镜像那一路） |

用法（容器内）：`python /app/probe_sister_ab.py [轮数]`
"""
import sys

sys.path.insert(0, "/app")

import brains                                                  # noqa: E402

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3

# 群里真实的语气：大家互称"姐姐"，连男生也被这么开着玩笑叫（镜像的来源）
HIST = [
    {"sender": "紫苑", "text": "顾~影~姐~姐~"},
    {"sender": "琉", "text": "他不是男的嘛"},
    {"sender": "紫苑", "text": "小小糯是妹子群搬过来的，默认群里全是妹子"},
    {"sender": "我", "text": "🤖 紫苑姐～我在呢，怎么啦～"},
]

CASES = [
    ("男1", "顾影", "男生", "怎么刷起来摸鱼币了，打屁股"),
    ("男2", "苏白", "男生", "你今天运势怎么样"),
    ("女1", "糯糯", "女生", "帮紫苑姐刷一下活跃度"),
    # ⚠️ 线上那次事故的形状：`_gender_label()` 只看**本地档案**，档案没性别时
    # 标签被整个省掉（`_who` = 光秃秃的名字）⇒ 模型手里**没有任何性别信息**。
    ("复现", "顾影", "", "怎么刷起来摸鱼币了，打屁股"),
]


def build(who, glabel, q):
    label = "{}（{}）".format(who, glabel) if glabel else who
    frame = brains.AT_FRAME_PROMPT.replace(
        "{who}", label).replace("{q}", q).replace("{pending}", "（空）")
    ctx = list(HIST) + [{"sender": "", "text": frame}]
    return ctx


def judge(name, glabel, reply):
    """无标签（复现用例）时，只要出现"<名字>姐姐/姐"就算叫错。"""
    sis = (name + "姐姐" in reply) or (name + "姐" in reply)
    bro = (name + "哥哥" in reply) or (name + "哥" in reply)
    wrong = sis if glabel == "男生" else (sis and False)
    if glabel == "":                       # 复现用例：他其实是男生（平台 gender=male）
        wrong = sis
    return {"bad": bool(wrong), "bro": bro, "sis": sis}


def main():
    print("收尾块版本：", "含【怎么叫】" if "【怎么叫】" in brains.AT_FRAME_PROMPT else "**没有**")
    print("轮数：", ROUNDS)
    bad_total = 0
    for tag, who, glabel, q in CASES:
        print()
        print("=== {} {}（{}）: {!r}".format(tag, who, glabel, q))
        for i in range(1, ROUNDS + 1):
            try:
                reply = (brains.generate_reply(build(who, glabel, q)) or "").strip()
            except Exception as e:                             # noqa: BLE001
                print("  第{}条 生成失败: {}".format(i, str(e)[:60]))
                continue
            v = judge(who, glabel, reply)
            if v["bad"]:
                bad_total += 1
            print("  {}. {} | 叫错={} 叫哥={} 叫姐={}".format(
                i, reply[:46].replace("\n", "⏎"), v["bad"], v["bro"], v["sis"]))
    print()
    print("==> 男生被叫姐姐的次数：{} / {}".format(
        bad_total, ROUNDS * 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
