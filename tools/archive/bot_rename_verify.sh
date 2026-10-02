#!/bin/sh
# 改名后的验证：@ 判定 / @ 剥离 / 回声过滤 都要对
echo "=== 1) 容器里生效的值 ==="
docker exec dzmm-bot sh -c 'echo "  DZMM_BOT_NAME  = [$DZMM_BOT_NAME]"; echo "  DZMM_BOT_SHORT = [$DZMM_BOT_SHORT]"; echo "  DZMM_BOT_ID    = [$DZMM_BOT_ID]"'

echo
echo "=== 2) self_names / @ 判定 / @ 剥离 ==="
docker exec -i dzmm-bot python - <<'PY'
import os
import sys
sys.path.insert(0, "/app")
os.chdir("/app")

import runtime.ctx as ctx
import runtime.identity as ident
import bot

ctx.set_active_cid("bd7fe6a4-0065-4551-ad3d-94116dba8f9c")
ident._load_member_names("bd7fe6a4-0065-4551-ad3d-94116dba8f9c")

print("  BOT_NAME       =", repr(ident.BOT_NAME))
print("  BOT_NAME_SHORT =", repr(ident.BOT_NAME_SHORT))
print("  self_names()   =", sorted(ident.self_names()))
print("  长名优先顺序   =", ident.self_names_longest_first())
print()

cases = [
    "@小小糯-bot 你好呀",
    "@小小糯～ 你好呀",
    "@小小糯 你好呀",
    "@小小糯询问昨晚发生什么",
    "随便聊聊",
]
print("  {:30s} {:>6s}  {}".format("文本", "is_at_me", "_strip_at"))
for t in cases:
    print("  {:30s} {:>6s}  {!r}".format(t, str(ident.is_at_me(t)), bot._strip_at(t)))

print()
print("  回声过滤：")
print("    is_self(BOT_UID)  =", ident.is_self(ident.BOT_UID))
print("    is_self(侧车 UID) =", ident.is_self(ident.NUO_UID))
print("    is_self(普通群友) =", ident.is_self("some-random-uid"))

print()
print("  残留检查（不该出现 '-bot' 或 '～' 残余）：")
bad = []
for t in cases[:3]:
    got = bot._strip_at(t)
    if "-bot" in got or "～" in got:
        bad.append((t, got))
print("   ", bad if bad else "无残余 ✓")
PY

echo
echo "=== 3) 公演「谁表现怎么样」的目标名也要干净 ==="
docker exec -i dzmm-bot python - <<'PY'
import os
import sys
sys.path.insert(0, "/app")
os.chdir("/app")
import runtime.fasao  # noqa: F401  （触发 runtime 包完整加载）
import runtime.identity as ident
import runtime.perf as perf

for t in ["@小小糯-bot 依依今天表现怎么样", "@小小糯～ 依依今天表现怎么样"]:
    got = perf._detect_perf_query(t)
    print("  {!r:34s} -> {!r}".format(t, got))
PY

echo
echo "=== 4) 改名前后的行为对比说明 ==="
echo "  改名前的隐患：'@小小糯-bot' 可能被剥成 '-bot'（set 迭代顺序不定）"
echo "  改后：长名优先，两个名字都剥得干净（见上面第 2 节）"

echo
echo "=== 5) 容器与自证行 ==="
docker inspect -f '  restarts={{.RestartCount}}' dzmm-bot
docker logs --tail 25 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:' | sed 's/^/  /'
echo -n "  异常命中: "
docker logs --tail 200 dzmm-bot 2>&1 | grep -icE 'traceback|exception'
