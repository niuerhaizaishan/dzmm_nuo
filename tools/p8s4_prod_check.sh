#!/bin/sh
# P8 第 4 步（模块拆分）部署后生产自检（宿主机执行，只读）
echo "=== container ==="
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker inspect -f 'restarts={{.RestartCount}} started={{.State.StartedAt}}' dzmm-bot
docker stats --no-stream dzmm-bot --format 'CPU={{.CPUPerc}} MEM={{.MemUsage}} ({{.MemPerc}})'

echo
echo "=== 启动自证行（P3/P4/P5/P7/P8-3） ==="
docker logs --tail 60 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:|心跳|sender ready|start  dry'

echo
echo "=== 异常扫描（最近 300 行） ==="
docker logs --tail 300 dzmm-bot 2>&1 | grep -iE 'traceback|exception|错误|失败' | head -20
echo "(以上为空 = 无异常)"

echo
echo "=== 拆分后的模块在容器内可用 ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")

import bot
import runtime.ctx as ctx
import runtime.dice as dice
import runtime.identity as ident
import runtime.obs as obs

print("bot.py 行数        :", sum(1 for _ in open("/app/bot.py", encoding="utf-8")))
print("runtime/dice.py 行数:", sum(1 for _ in open("/app/runtime/dice.py", encoding="utf-8")))

same = all(getattr(bot, n) is getattr(m, n) for n, m in [
    ("log", obs), ("get_name", ident), ("is_self", ident),
    ("_dice_state", dice), ("_dice_join_cmd", dice), ("_dice_current_player", dice),
])
print("re-export 同一对象 :", same)

bot._set_active_cid("probe-cid")
print("ctx 与镜像同步     :", ctx.get_active_cid() == bot._ACTIVE_CID == "probe-cid")

g = dice._dice_state()
print("骰子局状态可读     :", "dice_game" if g is None or isinstance(g, dict) else type(g))
print("  当前局面 keys   :", sorted(g.keys())[:8] if g else "（无进行中的局）")
PY

echo
echo "=== 出站队列 ==="
docker exec -i dzmm-bot python - <<'PY'
import sqlite3
c = sqlite3.connect("/app/data/outbox.db")
try:
    print("status:", dict(c.execute(
        "select status, count(*) from outbound group by status").fetchall()))
    print("attempt_count>1:", c.execute(
        "select count(*) from outbound where attempt_count > 1").fetchone()[0])
finally:
    c.close()
PY

echo
echo "=== 状态库里的骰子局是否完好（P7 的顶层键） ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
import state_store

raw = state_store.load_state()
dg = raw.get("dice_game")
print("顶层键 dice_game:", "有" if dg is not None else "无")
if dg:
    print("  active =", dg.get("active"), " phase =", dg.get("phase"),
          " players =", len(dg.get("players") or []))
print("群状态条目数     :", len([k for k in raw if k != "dice_game"]))
PY
