"""验证「/结束」收尾发骚不再被静默（真配置 + 真状态，只读不写业务状态）。

用法（服务器上）：
  docker exec dsh-... python /app/tools/verify_fasao_end.py
判定：
  - 发骚进行中 → `_cmd_owned_by_disabled` 必须是 False（会放行给 end_fasao）；
  - 没有进行中的发骚且骰子关着 → True（静默，与旧行为一致）。
"""
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import bot                                                    # noqa: E402
from runtime import features as F                             # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"                 # 上班如上坟，下班如还魂

print("=== 该群开关 ===")
for k in ("scene", "scene_end", "scene_continue", "insult", "dice", "diaojiao",
          "vote", "story", "perf"):
    try:
        print("  {:14s} {}".format(k, "开" if F.on(CID, k) else "关"))
    except Exception as e:                                     # noqa: BLE001
        print("  {:14s} ? {}".format(k, e))

print()
print("is_fasao_end_cmd('/结束') =", bot.is_fasao_end_cmd("/结束"))
print("骰子局在进行 =", bool(bot._dice_state().get("active")),
      "（True 会让骰子那一支的闸门打开，与本次无关但要知道）")

prev = bot._get_fasao_state(CID)
try:
    bot._set_fasao_state(CID, {})
    print()
    print("没有进行中的发骚 → 静默 =", bot._cmd_owned_by_disabled(CID, "/结束"),
          "（期望 True：没人接手，别当闲聊回）")

    bot._set_fasao_state(CID, {"active": True, "target_uid": "", "scene": "probe"})
    print("发骚进行中       → 静默 =", bot._cmd_owned_by_disabled(CID, "/结束"),
          "（期望 False：放行给 end_fasao）")

    for cmd in ("/停止", "/发骚结束"):
        print("发骚进行中 {} → 静默 =".format(cmd),
              bot._cmd_owned_by_disabled(CID, cmd), "（期望 False）")

    # 派发链那一支真的会接手吗？把它的三个条件照抄一遍（不跑分派链本身）：
    #   is_fasao_end_cmd(cmd) and _get_fasao_state(cid).active and _feat_on(cid, "scene_end")
    active = bool(bot._get_fasao_state(CID).get("active"))
    print("派发链条件：is_fasao_end_cmd=True / active={} / scene_end={} ⇒ 会进 end_fasao"
          .format(active, F.on(CID, "scene_end")))

    # 真跑一次 end_fasao（history 为空 ⇒ 不调模型，只出收尾话术），再复原状态
    bot._set_fasao_state(CID, {"active": True, "full": "验证用", "history": []})
    head = bot.end_fasao(CID)
    print("end_fasao 返回 =", repr(head)[:120])
    print("（有正文 ⇒ 群里会看到 💬 收尾，而不是 🔇 静默）")
finally:
    bot._set_fasao_state(CID, prev)                            # 复原原状态
    print()
    print("已复原发骚状态：", bot._get_fasao_state(CID))
