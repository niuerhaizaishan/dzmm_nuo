#!/bin/sh
# P8 第 4 步（模块拆分）最终生产自检（宿主机执行，只读）
echo "=== container ==="
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker inspect -f 'restarts={{.RestartCount}} started={{.State.StartedAt}}' dzmm-bot
docker stats --no-stream dzmm-bot --format 'CPU={{.CPUPerc}} MEM={{.MemUsage}} ({{.MemPerc}})'

echo
echo "=== 启动自证行 ==="
docker logs --tail 60 dzmm-bot 2>&1 | grep -E 'bot路由|outbox:|P5:|P7:|P8:|心跳|sender ready|start  dry'

echo
echo "=== 异常扫描（最近 300 行） ==="
docker logs --tail 300 dzmm-bot 2>&1 | grep -iE 'traceback|exception|错误|失败' | head -20
echo "(以上为空 = 无异常)"

echo
echo "=== 结构：搬迁后的规模与不变量 ==="
docker exec -i dzmm-bot python - <<'PY'
import ast
import sys
sys.path.insert(0, "/app")

import bot

runtime_files = sorted(p for p in __import__("pathlib").Path("/app/runtime").glob("*.py"))
bot_lines = sum(1 for _ in open("/app/bot.py", encoding="utf-8"))
print("bot.py 行数        :", bot_lines)
print("runtime 模块数     :", len(runtime_files))

# 自动推导"已搬走"：模块里定义了、且 bot 上是同一个对象
auto = 0
for p in runtime_files:
    if p.stem == "__init__":
        continue
    mod = __import__("runtime." + p.stem, fromlist=["x"])
    names = {n.name for n in ast.parse(p.read_text(encoding="utf-8")).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for n in names:
        if getattr(bot, n, None) is getattr(mod, n, None):
            auto += 1
print("自动推导已搬函数   :", auto)

# 两边不得同名定义
bot_defs = {n.name for n in ast.parse(
    open("/app/bot.py", encoding="utf-8").read()).body
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
clash = {}
for p in runtime_files:
    names = {n.name for n in ast.parse(p.read_text(encoding="utf-8")).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if names & bot_defs:
        clash[p.name] = sorted(names & bot_defs)
print("同名重复定义       :", clash or "无")

refs = []
for p in runtime_files:
    for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if ln.strip().startswith(("import bot", "from bot import")):
            refs.append((p.name, i))
print("runtime 回指 bot.py:", refs or "无")

# 模块级名字重复定义（曾真实发生：header 与搬运各一份）
dup = {}
for p in runtime_files:
    seen = {}
    for n in ast.parse(p.read_text(encoding="utf-8")).body:
        ns = []
        if isinstance(n, ast.Assign):
            ns = [t.id for t in n.targets if isinstance(t, ast.Name)]
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            ns = [n.target.id]
        for nm in ns:
            seen.setdefault(nm, []).append(n.lineno)
    d = {k: v for k, v in seen.items() if len(v) > 1}
    if d:
        dup[p.name] = d
print("模块级重复定义     :", dup or "无")

bot._set_active_cid("probe-cid")
import runtime.ctx as ctx
print("ctx 与镜像同步     :", ctx.get_active_cid() == bot._ACTIVE_CID == "probe-cid")
bot._set_active_cid("")
PY

echo
echo "=== 四个状态机谓词 & 常量（生产镜像里真跑） ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")

import runtime.diaojiao as dj
import runtime.fasao as fasao
import runtime.story as story
import runtime.vote as vote

ok = True


def chk(label, cond, extra=""):
    global ok
    print("  [{}] {}{}".format("OK" if cond else "!!", label,
                               (" -- " + str(extra)) if extra else ""))
    ok = ok and bool(cond)


chk("/投票结束 同时命中开始与结束（保全清单 #1）",
    vote.is_vote_start_cmd("/投票结束") is True
    and vote.is_vote_end_cmd("/投票结束") is True)
chk("/发骚结束 同时命中发骚/场景/结束（保全清单 #2）",
    fasao.is_fasao_cmd("/发骚结束") is True
    and fasao.is_scene_cmd("/发骚结束") is True
    and fasao.is_fasao_end_cmd("/发骚结束") is True)
chk("/调教结束：开始判定把「结束」当目标名（保全清单 #3）",
    dj.is_diaojiao_cmd("/调教结束") == "结束" and dj.is_dj_end_cmd("/调教结束") is True)
chk("/剧情结束 只有结束命中（澄清：无重叠）",
    story.is_story_start_cmd("/剧情结束") is False
    and story.is_story_end_cmd("/剧情结束") is True)
chk("DJ_PARTICIPATE_HINT 完整（曾被抄漏后半句）",
    "30秒没人我就自己继续啦" in dj.DJ_PARTICIPATE_HINT)

scene, pos, extra = fasao._pick_combo_by_xp(["露出"])
chk("按 XP 定制：露出 → 命中的场景/附加在偏好表内",
    scene in fasao.XP_BIAS["露出"]["scene"]
    and extra in fasao.XP_BIAS["露出"]["extra"] and pos in fasao.POSITIONS)

print()
print("抽查结果:", "全部通过" if ok else "**有失败**")
raise SystemExit(0 if ok else 1)
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
echo "=== 状态库（P7 顶层键 + 各群状态） ==="
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app")
import state_store

raw = state_store.load_state()
dg = raw.get("dice_game")
print("顶层键 dice_game:", "有" if dg is not None else "无",
      "| active =", (dg or {}).get("active"))
keys = set()
for cid, g in raw.items():
    if isinstance(g, dict):
        keys |= set(g.keys())
print("群状态里出现过的键:", sorted(keys))
print("群状态条目数      :", len([k for k in raw if k != "dice_game"]))
PY
