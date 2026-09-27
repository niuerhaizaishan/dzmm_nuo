#!/bin/sh
# P12-S 上线自检：Bot 改名（薇丝-bot -> 薇丝）后，身份判定/剥 @ 是否都对
docker exec -i -e PYTHONIOENCODING=utf-8 dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
import bot
from runtime import identity as ident

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"

cfg = bot._CFG
print("配置里的 Bot：")
for b in cfg.bots:
    print("  key={:10s} name={!r} short={!r} uid={}".format(
        b.key, b.name, b.short, b.uid))
ident.register_bots(cfg.bots)

print("bot_names() =", sorted(ident.bot_names()))
print("self_names() 里含「薇丝」的 =", sorted([n for n in ident.self_names() if "薇丝" in n]))
print("玫瑰房 self_names_for 含「薇丝」的 =",
      sorted([n for n in ident.self_names_for(ROSE) if "薇丝" in n]))

print("--- 认不认「在叫我」（两种写法都要认）---")
for t in ("薇丝 你好", "薇丝-bot 你好", "@薇丝 你好", "@薇丝-bot 你好", "@路人甲 你好"):
    print("  {:16s} mentions_self={:5s} is_at_me={}".format(
        t, str(ident.mentions_self(t, ROSE)), str(ident.is_at_me(t, ROSE))))

print("--- 剥 @ 前缀（不能留 -bot 残余）---")
for t in ("@薇丝-bot 你好呀", "@薇丝 你好呀", "@薇丝bot 你好呀"):
    print("  {:16s} -> {!r}".format(t, bot._strip_at(t, ROSE)))

print("--- 自己发的消息仍然认得出 ---")
for uid in (ident.NUO_UID,) + tuple(sorted(ident.bot_uids())):
    print("  is_self({}) = {}".format(str(uid)[:8], ident.is_self(uid)))
PY
echo "=== 启动行 ==="
grep -a 'Bot key=' /opt/dzmm/bot.log | tail -2
