"""在容器内确认「自身身份」配置正确（P4.2 的安全属性）。

关键：`BOT_UID` 必须非空。否则小小糯会把自己的 Bot 发言当成群友 ——
轻则统计污染，重则自触发循环。

    docker cp tools/bot_identity_check.py dzmm-bot:/tmp/
    docker exec dzmm-bot python /tmp/bot_identity_check.py
"""
import bot

print("BOT_UID 非空  :", bool(bot.BOT_UID))
print("BOT_UID       :", bot.BOT_UID)
print("self_uids     :", sorted(bot.self_uids()))
print("is_self(Bot)  :", bot.is_self(bot.BOT_UID))
print("is_self(账号) :", bot.is_self(bot.NUO_UID))
print("is_self(群友) :", bot.is_self("some-user"))
print("self_names    :", sorted(bot.self_names()))

assert bot.BOT_UID, "BOT_UID 为空 —— Bot 发言会被当成群友，必须配 DZMM_BOT_ID"
assert bot.is_self(bot.BOT_UID) is True
assert bot.is_self(bot.NUO_UID) is True
assert bot.is_self("some-user") is False
print()
print("OK: 身份识别正确")
