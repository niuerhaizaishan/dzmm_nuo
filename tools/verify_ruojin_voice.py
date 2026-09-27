"""P12-T 验证：若瑾的羞辱/发骚口吻（真实调用 LLM，只在容器内打印，不发群）。"""
import sys

sys.path.insert(0, "/app")

import brains                     # noqa: E402
import personas                   # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"

personas.set_config_personas({CID: "ruojin"})
personas.set_cid(CID)             # 让 apply_persona 用若瑾那套（含覆盖声明）
print("本群自称 =", personas.me(CID))
print("发骚文案（本群）：")
for k in ("fasao_not_found", "fasao_empty", "fasao_pick", "fasao_fallback",
          "fasao_end", "scene_default_who"):
    print("  {:20s} {!r}".format(k, personas.p(k, CID, who="小张")))

print()
print("========== 羞辱 · 目标=男性（代码级过滤路径：straight=True）==========")
male = ("昵称：小张\n性别：男\n签名：健身教练，撸铁十年\n"
        "简介：嘴上没输过，手上没赢过\n最近发言：今天又被老板骂了 / 我也没办法啊")
try:
    text, style = brains.generate_insult(male, gender="男生", straight=True)
    print("风格：", style)
    print("（用的风格库：{}）".format(
        "只毒舌那套" if style in brains.INSULT_STYLES_PLAIN else "露骨那套"))
    print(text)
except Exception as e:                                      # noqa: BLE001
    print("失败：{}: {}".format(type(e).__name__, e))

print()
print("========== 羞辱 · 目标=女性（代码级过滤路径：straight=True）==========")
female = ("昵称：阿雅\n性别：女\n签名：爱笑的设计师\n"
          "简介：谁惹我我就拉黑谁\n最近发言：这稿子又被打回来了 / 烦死了")
try:
    text, style = brains.generate_insult(female, gender="女生", straight=True)
    print("风格：", style)
    print("（用的风格库：{}）".format(
        "只毒舌那套 ✓" if style in brains.INSULT_STYLES_PLAIN else "露骨那套 ✗"))
    print(text)
    dirty = [w for w in ("骚穴", "肉便器", "淫荡", "发情", "母狗", "腿心", "浪货", "湿") if w in text]
    print("【自检】露骨词命中：", dirty or "无 ✓")
except Exception as e:                                      # noqa: BLE001
    print("失败：{}: {}".format(type(e).__name__, e))
