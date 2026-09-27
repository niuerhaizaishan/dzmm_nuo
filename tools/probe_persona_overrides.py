"""看 config.json 里的人设文本覆盖（改了代码默认值后，确认线上到底用哪一份）。"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

raw = json.load(open("/app/config/config.json", encoding="utf-8"))
per = raw.get("personas") or {}
print("personas 覆盖的人设：", sorted(per.keys()))
for key, val in per.items():
    if not isinstance(val, dict):
        print("  {}（非 dict）".format(key))
        continue
    print("  {} 字段：{}".format(key, sorted(val.keys())))
    txt = val.get("text") or {}
    if isinstance(txt, dict):
        for k, v in txt.items():
            print("     text.{} 长度={} {}…".format(
                k, len(str(v)), str(v)[:80].replace("\n", "⏎")))
    phrases = val.get("phrases") or {}
    if phrases:
        print("     phrases：{} 条".format(len(phrases)))

import personas as P                                          # noqa: E402
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
extra = P.persona_for(ROSE)["extra"]
print()
print("玫瑰房最终生效的 extra 长度 =", len(extra))
print("  含 4～8 句：", "4～8 句" in extra, " 含 4～10 句：", "4～10 句" in extra)
print("  含『10 行』：", "10 行" in extra, " 含『20 行』：", "20 行" in extra)
i = extra.find("篇幅")
print("  篇幅段：", extra[i:i + 200].replace("\n", "⏎") if i >= 0 else "（没有）")
