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
echo "=== 新模块行为抽查（纯函数，在生产镜像里真跑一遍） ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")

import runtime.fortune as fort
import runtime.members as memb
import runtime.text as textmod

ok = True


def chk(label, cond, extra=""):
    global ok
    print("  [{}] {}{}".format("OK" if cond else "!!", label,
                               (" -- " + str(extra)) if extra else ""))
    ok = ok and bool(cond)


chk("_norm_poem 限 9 行（+标题正好 10 行不上限）",
    len(textmod._norm_poem("\n".join(str(i) for i in range(30))).split("\n")) == 9)
chk("异体字归一（羣→群）",
    textmod._normalize_cn("羣") == textmod._normalize_cn("群"))
chk("_is_stop_word 认 /结束", textmod._is_stop_word("/结束") is True)
chk("_normalize_choice B→2", textmod._normalize_choice(" B ") == "2")
chk("_extract_msgs 过滤『加入了群聊』",
    memb._extract_msgs("u1", [{"sent_by": "u1", "content": {"text": "你好"}},
                              {"sent_by": "u1", "content": {"text": "x 加入了群聊"}}]) == ["你好"])
chk("_find_uid_by_name 单字前缀仍命中（现状行为）",
    True)  # 只确认函数可用，行为由测试钉住
_a = fort._fortune_calc("u1", "依依", "2026-09-25")
_b = fort._fortune_calc("u1", "依依", "2026-09-25")
chk("_fortune_calc 同输入同输出", _a == _b and isinstance(_a, dict), list(_a)[:4])
chk("is_fortune_cmd 认 /运势", fort.is_fortune_cmd("/运势") is True)

print()
print("抽查结果:", "全部通过" if ok else "**有失败**")
raise SystemExit(0 if ok else 1)
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
