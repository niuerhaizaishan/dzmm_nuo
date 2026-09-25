#!/bin/sh
# P11 A 期上线自检：配置层是否零行为变化
echo "=== 1) 部署前：确认服务器上没有 config.json（走默认值路径） ==="
ls -l /opt/dzmm/config.json 2>/dev/null || echo "  没有 config.json —— 会用 bot.py 里的默认值"

echo
echo "=== 2) 启动自证行（现在应该有 P11 那条） ==="
docker logs --tail 40 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:|P11:|心跳|sender ready|start  dry' | sed 's/^/  /'

echo
echo "=== 3) 生效的群清单（用 dashboard 的 config 段读，它读的就是启动时那一份） ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")
import dashboard as d

cfg = d.section_config()
print("  source =", cfg.get("source"), "| path =", cfg.get("path"))
print("  群 =", len(cfg.get("GROUPS") or []))
for g in cfg.get("groups") or []:
    print("    {}  alias={!r} mute={} screen={} push={} bot={!r}".format(
        g["cid"][:8], g["alias"], g["mute"], g["screen"], g["push"], g["bot"]))
print("  Bot 列表:")
for b in cfg.get("bots") or []:
    print("    key={} name={!r} cid={} token_env={} token_set={}".format(
        b["key"], b["name"], b["cid"][:8], b["token_env"], b["token_set"]))
print("  未求值的默认值:", [u["name"] for u in cfg.get("unresolved") or []])
PY

echo
echo "=== 4) 与改动前的期望值逐项比对（零行为变化的证据） ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
import bot

EXPECT_GROUPS = ['bd7fe6a4-0065-4551-ad3d-94116dba8f9c',
                 '93cc6c7b-ba9e-48f3-a106-3074483aebbd',
                 '38698306-4252-438a-aa56-7dbab89250eb',
                 '007bfd75-2efb-491c-8233-20046af69b24',
                 '4a56be71-9ca6-4785-b45f-18725d187f1a']
EXPECT_SCREEN = {'bd7fe6a4-0065-4551-ad3d-94116dba8f9c',
                 '93cc6c7b-ba9e-48f3-a106-3074483aebbd',
                 '38698306-4252-438a-aa56-7dbab89250eb'}
EXPECT_PUSH = ['bd7fe6a4-0065-4551-ad3d-94116dba8f9c',
               '007bfd75-2efb-491c-8233-20046af69b24']

checks = [
    ("GROUPS 顺序与内容一致", bot.GROUPS == EXPECT_GROUPS),
    ("MUTE_CIDS 为空", bot.MUTE_CIDS == set()),
    ("SCREEN_CIDS 一致", bot.SCREEN_CIDS == EXPECT_SCREEN),
    ("PUSH_CIDS 一致", bot.PUSH_CIDS == EXPECT_PUSH),
    ("_ACTIVE_CID == GROUPS[0]", bot._ACTIVE_CID == EXPECT_GROUPS[0]),
    ("Bot 注册表 key=nuo_main", [b.key for b in bot._CFG.bots] == ["nuo_main"]),
    ("Bot 绑认证群", bot._CFG.bots[0].cid == EXPECT_GROUPS[0]),
    ("Bot token 能从 env 取到", bool(bot._CFG.bots[0].resolve_token())),
    ("Bot 名字是配置里的值", bot._CFG.bots[0].name == bot.BOT_NAME),
]
bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print("  [{}] {}".format("OK" if ok else "!!", n))
print()
print("结论:", "零行为变化 ✓" if not bad else "**有差异**: {}".format(bad))
raise SystemExit(1 if bad else 0)
PY
