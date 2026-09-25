#!/bin/sh
# 配置页在生产上的端到端验收（用**无副作用**的改动：群别名）
#
# 为什么用别名做真实验收：别名"只用于显示，不参与任何判定"（代码里就这么写的），
# 所以整条写路径（校验→备份→审计→重启→生效）都能验到，而线上行为一点不变。
#
# ★ 两个坑，都踩过，都写在这里免得再踩：
#   1. 路径：配置挪进 /opt/dzmm/config/ 之后（目录挂载，见 runtime/store.py 的
#      os.replace 说明），配置文件=/opt/dzmm/config/config.json、
#      备份=/opt/dzmm/config/config_backups/、审计=/opt/dzmm/config/config_audit.log。
#   2. **复选框必须按 checked 判**：`/edit` 是整表提交，如果拼 body 时把没勾的
#      复选框也塞进去，一次保存就会顺手把别的群的开关全翻成 true。
#      第一版就是无条件收 name/value 对，结果改一个别名顺带翻了 4 个群的
#      静默/筛查/推送。所以下面：只收带 checked 的复选框，并且**逐字段比对**
#      断言"除了别名一个字都没动"。
CFG=/opt/dzmm/config/config.json
BK=/opt/dzmm/config/config_backups
AUDIT=/opt/dzmm/config/config_audit.log
PW=$(grep -E '^DZMM_ADMIN_PASSWORD=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
USER=$(grep -E '^DZMM_ADMIN_USER=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
USER=${USER:-admin}
B=http://127.0.0.1:8090
J=/tmp/cfg_cookies.txt
FAIL=0

# ★ 重启容器会把后台的会话一起清掉（会话是**进程内存**里的），
#   所以每轮重启之后都得重新登录 —— 第一版没重登，第 6 步拿到的是登录页，
#   表现为"页面上没有备份可选 / 回滚 200 但没生效"。
login() {
  rm -f $J
  code=$(curl -s -o /dev/null -c $J -w '%{http_code}' \
         -d "username=$USER" -d "password=$PW" $B/login)
  [ "$code" = "303" ] && echo "  登录 -> $code ✓" || { echo "  登录 -> $code !! 失败"; FAIL=1; }
}

echo "=== 0) 保存前的基线 ==="
# 幂等：上一轮要是没回滚干净，先把别名掰回去，再开始本轮验收
if ! grep -q '认证妹子群"' $CFG; then
  echo "  （上一轮残留：先把别名改回「认证妹子群」）"
  python3 - <<'PY'
import json
p = '/opt/dzmm/config/config.json'
c = json.load(open(p, encoding='utf-8'))
for g in c['groups']:
    if g['cid'].startswith('bd7fe6a4'):
        g['alias'] = '认证妹子群'
open(p, 'w', encoding='utf-8').write(
    json.dumps(c, ensure_ascii=False, indent=2) + '\n')
PY
  cd /opt/dzmm && docker compose restart dzmm-bot >/dev/null 2>&1
  sleep 13
fi
cp $CFG /tmp/cfg_before.json
python3 - <<'PY'
import json
c = json.load(open('/tmp/cfg_before.json', encoding='utf-8'))
for g in c['groups']:
    print("  {} mute={} screen={} push={}".format(g['alias'][:10], g['mute'], g['screen'], g['push']))
PY

echo
echo "=== 1) 未登录访问 /edit ==="
code=$(curl -s -o /tmp/e0.html -w '%{http_code}' $B/edit)
grep -q "请登录" /tmp/e0.html && echo "  $code（登录页）✓" || { echo "  $code !! 泄漏"; FAIL=1; }

echo
echo "=== 2) 登录并打开配置页 ==="
login
curl -s -b $J -o /tmp/e1.html -w '  /edit -> %{http_code}  %{size_download} bytes\n' $B/edit
grep -q "保存配置" /tmp/e1.html && echo "  表单渲染 ✓" || { echo "  !! 没有表单"; FAIL=1; }
grep -q "尚未生效" /tmp/e1.html && echo "  （提示：有改动未生效）" || echo "  （当前没有未生效改动）"

echo
echo "=== 3) 改一个别名（无副作用）并保存 ==="
# 同时兼容 name='x' 与 name=x 两种写法 —— 第一版只认带引号的，
# 结果表单里根本没带上 csrf，被服务端正确拒了（400）。
CSRF=$(grep -o "name=['\"]\?csrf['\"]\? value='[^']*'" /tmp/e1.html | head -1 | sed "s/.*value='\([^']*\)'.*/\1/")
echo "  csrf: ${CSRF:0:12}…"
[ -n "$CSRF" ] || { echo "  !! 没抓到 csrf"; exit 1; }
# 目标别名带秒数：保证每一轮的"改动"都是真的改动（否则重复跑会变成"没有改动"）
TARGET="认证妹子群（配置页验收 $(date +%H%M%S)）"
echo "  目标别名: $TARGET"
python3 - "$CSRF" "$TARGET" <<'PY' > /tmp/form.txt
import re, sys, urllib.parse
csrf, target = sys.argv[1], sys.argv[2]
html = open('/tmp/e1.html', encoding='utf-8').read()
# 只取真正那个 <form action='/edit'>，别把下面回滚按钮的小表单也扫进来
m = re.search(r"<form[^>]*action='/edit'[^>]*>(.*?)</form>", html, re.S)
if not m:
    sys.exit("找不到 /edit 表单")
out, skipped = [], 0
for tag in re.findall(r"<input\b[^>]*>", m.group(1)):
    nm = re.search(r"name=['\"]?([^'\"\s>]+)", tag)
    if not nm:
        continue
    k = nm.group(1)
    # ★ 属性是带引号的（type='checkbox'）—— 第一版写成 "type=checkbox" in tag，
    #   永远不成立，于是"跳过未勾选"一个都没跳过，等于没修。
    if re.search(r"type=['\"]?checkbox", tag) and not re.search(r"\bchecked\b", tag):
        skipped += 1
        continue                      # ★ 没勾的复选框浏览器不会提交，这里也必须不提交
    vm = re.search(r"value='([^']*)'", tag)
    v = vm.group(1) if vm else ""
    if k == 'csrf':
        v = csrf
    elif k == 'groups[0][alias]':
        v = target
    out.append((k, v))
if skipped == 0:
    sys.stderr.write("  !! 一个复选框都没跳过 —— 说明识别复选框失败了，"
                     "这样会把没勾的开关也提交上去\n")
    sys.exit("复选框识别失败")
sys.stderr.write("  提交字段 {} 个（按 checked 跳过未勾选 {} 个）\n".format(len(out), skipped))
print(urllib.parse.urlencode(out))
PY
grep -q 'csrf=' /tmp/form.txt || { echo "  !! 表单里没有 csrf"; exit 1; }
curl -s -b $J -o /tmp/e2.html -w '  保存 -> %{http_code}\n' --data-binary @/tmp/form.txt $B/edit
grep -q "已保存" /tmp/e2.html && echo "  页面报「已保存」✓" || { echo "  !! 没保存成功"; grep -o "msg bad'>[^<]*" /tmp/e2.html | head -2; FAIL=1; }

echo
echo "=== 4) 落盘 / 备份 / 审计 / **只动了别名** ==="
grep -q "配置页验收" $CFG && echo "  config.json 已更新 ✓" || { echo "  !! 文件没变"; FAIL=1; }
echo -n "  备份数: "; ls -1 $BK/config-*.json 2>/dev/null | wc -l
echo -n "  审计最后一行: "; tail -1 $AUDIT 2>/dev/null | cut -c1-140
python3 - <<'PY'
import json
# 和 config_ui.diff_summary 一样：字段没写 = 取默认值，别把"补默认值"当改动
GD = {"alias": "", "enabled": True, "mute": False, "screen": False, "push": False,
      "persona": ""}
BD = {"name": "", "short": "", "cid": "", "token_env": "", "persona": "",
      "enabled": True}

def norm(x, d):
    x = dict(x or {})
    for k, v in d.items():
        x.setdefault(k, v)
    return x

a = json.load(open('/tmp/cfg_before.json', encoding='utf-8'))
b = json.load(open('/opt/dzmm/config/config.json', encoding='utf-8'))
ga = {g['cid']: norm(g, GD) for g in a['groups']}
gb = {g['cid']: norm(g, GD) for g in b['groups']}
diffs = []
for c in sorted(set(ga) | set(gb)):
    x, y = ga.get(c), gb.get(c)
    if x is None or y is None:
        diffs.append((c[:8], 'added/removed', None, None))
        continue
    for k in sorted(set(x) | set(y)):
        if x.get(k) != y.get(k):
            diffs.append((c[:8], k, x.get(k), y.get(k)))
ba = {x['key']: norm(x, BD) for x in a.get('bots') or []}
bb = {x['key']: norm(x, BD) for x in b.get('bots') or []}
if ba != bb:
    diffs.append(('bots', 'changed', ba, bb))
for d in diffs:
    print("   ", d)
bad = [d for d in diffs if d[1] != 'alias']
print("  ✓ 只有别名变化（其它字段一字未动）" if not bad
      else "  !! 出现了别名以外的改动 —— 整表提交把别的字段带歪了")
raise SystemExit(1 if bad else 0)
PY
[ $? -eq 0 ] || FAIL=1

echo
echo "=== 5) 重启让它生效 ==="
BEFORE=$(docker inspect -f {{.State.StartedAt}} dzmm-bot)
cd /opt/dzmm && docker compose restart dzmm-bot >/dev/null 2>&1
sleep 13
AFTER=$(docker inspect -f {{.State.StartedAt}} dzmm-bot)
[ "$BEFORE" != "$AFTER" ] && echo "  确实重启了（StartedAt $BEFORE -> $AFTER）✓" \
    || { echo "  !! 没重启，StartedAt 没变（$BEFORE）"; FAIL=1; }
docker logs --tail 40 dzmm-bot 2>&1 | grep -E 'P11:|bot路由|P8:' | sed 's/^/  /'
# 最要紧的一条：文件改了 → 进程加载了 → **生效的开关数量与配置文件一致**
# （整表提交一旦把别的群开关带歪，或把某个群整行删掉，这里立刻会不一致）
EXPECT=$(python3 - <<'PY'
import json
c = json.load(open('/opt/dzmm/config/config.json', encoding='utf-8'))
g = [x for x in c['groups'] if x.get('enabled', True)]
print("群={} 静默={} 筛查={} 推送={} Bot={}".format(
    len(g), sum(1 for x in g if x.get('mute')),
    sum(1 for x in g if x.get('screen')), sum(1 for x in g if x.get('push')),
    len(c.get('bots') or [])))
PY
)
echo "  （按配置文件推导，应当生效的口径：$EXPECT）"
P11=$(docker logs --tail 40 dzmm-bot 2>&1 | grep -o 'P11: 配置=file 群=[0-9]* 静默=[0-9]* 筛查=[0-9]* 推送=[0-9]* Bot=[0-9]*' | tail -1)
case "$P11" in
  *"$EXPECT"*) echo "  ✓ 生效口径与配置文件一致（$P11）" ;;
  *) echo "  !! 生效口径与配置文件不一致：进程=${P11:-（没抓到）} 文件=$EXPECT"; FAIL=1 ;;
esac
echo "  群数:"
docker exec -i dzmm-bot python - <<'PY'
import sys
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/admin")
import dashboard as d
cfg = d.section_config()
hit = [g for g in cfg["groups"] if g["cid"].startswith("bd7fe6a4")]
print("    面板里读到的别名:", hit[0]["alias"] if hit else "(没有)")
print("    配置里的群:", cfg["configured_count"], "| 生效的群:", cfg["group_count"],
      "| source:", cfg["source"])
print("    页面上会渲染的群行数（含停用）:", len(cfg["groups"]))
for g in cfg["groups"]:
    print("     ", "✔" if g["enabled"] else "✘", g["cid"][:8], g["alias"][:18])
PY

echo
echo "=== 6) 回滚到上一版（回到原别名） ==="
login                      # ★ 第 5 步重启把会话清了，这里必须重登
curl -s -b $J -o /tmp/e3.html $B/edit
echo "  /edit 页面 $(wc -c < /tmp/e3.html) 字节，回滚表单 $(grep -c "action='/edit/rollback'" /tmp/e3.html) 个"
CSRF2=$(grep -o "name=['\"]\?csrf['\"]\? value='[^']*'" /tmp/e3.html | head -1 | sed "s/.*value='\([^']*\)'.*/\1/")
# 上一版 = 本次保存**之前**刚做的那份备份（列在页面上的第一个）。
# 用 grep/sed 取，不用 python —— 与 csrf 同一个写法，少一层引号风险。
BK1=$(grep -o "name='name' value='[^']*'" /tmp/e3.html | head -1 | sed "s/.*value='\([^']*\)'.*/\1/")
echo "  回滚到: ${BK1:-（空）}"
[ -n "$BK1" ] || { echo "  !! 页面上没有备份可选"; FAIL=1; }
curl -s -b $J -o /tmp/e4.html -w '  回滚 -> %{http_code}\n' --data-urlencode "csrf=$CSRF2" --data-urlencode "name=$BK1" $B/edit/rollback
grep -q "已回滚" /tmp/e4.html && echo "  页面报「已回滚」✓" || { echo "  !! 回滚失败"; grep -o "msg bad'>[^<]*" /tmp/e4.html | head -2; FAIL=1; }
grep -q "配置页验收" $CFG && { echo "  !! 别名还在（回滚没生效）"; FAIL=1; } || echo "  别名已还原 ✓"
python3 - <<'PY'
import json
GD = {"alias": "", "enabled": True, "mute": False, "screen": False, "push": False,
      "persona": ""}
BD = {"name": "", "short": "", "cid": "", "token_env": "", "persona": "",
      "enabled": True}

def norm(x, d):
    x = dict(x or {})
    for k, v in d.items():
        x.setdefault(k, v)
    return x

a = json.load(open('/tmp/cfg_before.json', encoding='utf-8'))
b = json.load(open('/opt/dzmm/config/config.json', encoding='utf-8'))
same = ([norm(g, GD) for g in a['groups']] == [norm(g, GD) for g in b['groups']]
        and [norm(x, BD) for x in a.get('bots') or []]
        == [norm(x, BD) for x in b.get('bots') or []])
print("  ✓ 回滚后与保存前逐字段一致（开关没被带歪）" if same
      else "  !! 回滚后与保存前不一致")
raise SystemExit(0 if same else 1)
PY
[ $? -eq 0 ] || FAIL=1

cd /opt/dzmm && docker compose restart dzmm-bot >/dev/null 2>&1
sleep 13
echo
echo "=== 7) 最终状态 ==="
docker logs --tail 25 dzmm-bot 2>&1 | grep -E 'P11:|bot路由|P8:|心跳|start  dry' | sed 's/^/  /'
echo -n "  异常命中: "
docker logs --tail 200 dzmm-bot 2>&1 | grep -icE 'traceback|exception'
echo
[ $FAIL -eq 0 ] && echo "===> 配置页端到端验收：全部通过 ✓" || echo "===> 配置页端到端验收：**有失败项** ✗"
if [ $FAIL -eq 0 ]; then
  rm -f $J /tmp/e0.html /tmp/e1.html /tmp/e2.html /tmp/e3.html /tmp/e4.html /tmp/form.txt /tmp/cfg_before.json
else
  echo "  （失败：现场留在 /tmp/e1.html /tmp/e3.html /tmp/e4.html /tmp/form.txt /tmp/cfg_before.json）"
fi
exit $FAIL
