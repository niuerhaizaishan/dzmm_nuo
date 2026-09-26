#!/bin/sh
# P12-D 生产验收：功能与参数页（矩阵 / 参数 / CSP / 静态 JS / 保存真生效）
#
# 用**无害的**改动做真实验收：把 `devil.cooldown` 600 → 601。
# 为什么无害：主动吐槽默认是关的（群状态 devil_enabled 没开），所以改它的冷却
# 对线上行为没有影响；但它是一条**真的写入**（走完整校验+备份+审计+重启生效）。
PW=$(grep -E '^DZMM_ADMIN_PASSWORD=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
USER=$(grep -E '^DZMM_ADMIN_USER=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
B=http://127.0.0.1:8090
J=/tmp/p12d_cookies.txt
CFG=/opt/dzmm/config/config.json
AUDIT=/opt/dzmm/config/config_audit.log
FAIL=0
rm -f $J

echo "=== 1) 未登录访问 /features ==="
code=$(curl -s -o /tmp/d0.html -w '%{http_code}' $B/features)
grep -q "请登录" /tmp/d0.html && echo "  $code（登录页）✓" || { echo "  $code !! 泄漏"; FAIL=1; }

echo
echo "=== 2) 登录 + 矩阵页渲染 ==="
curl -s -o /dev/null -c $J -w '  登录 -> %{http_code}\n' -d "username=$USER" -d "password=$PW" $B/login
curl -s -b $J -o /tmp/d1.html -w '  /features -> %{http_code}  %{size_download} bytes\n' $B/features
echo -n "  功能行数（应有 64 个 gfeat[<key>]）: "
grep -o "gfeat\[[a-z_]*\]" /tmp/d1.html | sort -u | wc -l
echo -n "  参数行数: "; grep -o "gtune\[[a-z_.]*\]" /tmp/d1.html | sort -u | wc -l
echo -n "  按群集合: "; grep -o "scope_seen\[[a-z]*\]" /tmp/d1.html | sort -u | tr '\n' ' '; echo
echo -n "  强制复位按钮: "; grep -c "action='/edit/reset'" /tmp/d1.html
grep -q "feat_form" /tmp/d1.html && echo "  防丢失标记 ✓" || { echo "  !! 缺 feat_form 标记"; FAIL=1; }

echo
echo "=== 3) CSP 与静态 JS ==="
curl -s -b $J -D /tmp/d1.hdr -o /dev/null $B/features
echo -n "  CSP: "; grep -i '^content-security-policy' /tmp/d1.hdr | cut -d' ' -f2- | tr -d '\r'
echo -n "  X-Frame-Options: "; grep -i '^x-frame-options' /tmp/d1.hdr | cut -d' ' -f2- | tr -d '\r'
curl -s -b $J -o /tmp/app.js -w '  /static/app.js -> %{http_code}  %{size_download} bytes\n' $B/static/app.js
grep -q textContent /tmp/app.js && echo "  脚本用 textContent ✓" || { echo "  !! 脚本异常"; FAIL=1; }
code=$(curl -s -b $J -o /dev/null -w '%{http_code}' $B/static/nope.js)
[ "$code" = "404" ] && echo "  静态白名单 ✓（nope.js -> 404）" || { echo "  !! 白名单没生效: $code"; FAIL=1; }

echo
echo "=== 4) 保存一个无害覆盖（devil.cooldown 600 -> 601） ==="
# 幂等：上一轮如果留下过这个覆盖，先清掉再验（否则这一步会变成"没有改动"）
LEFTOVER=$(python3 -c "
import json;d=json.load(open('$CFG',encoding='utf-8'));print((d.get('tuning') or {}).get('devil.cooldown',''))")
if [ -n "$LEFTOVER" ]; then
  echo "  （上一轮残留 devil.cooldown=$LEFTOVER，先清掉）"
  C0=$(grep -o "name=['\"]\?csrf['\"]\? value='[^']*'" /tmp/d1.html | head -1 | sed "s/.*value='\([^']*\)'.*/\1/")
  curl -s -o /dev/null -b $J --data-urlencode "csrf=$C0" -d "feat_form=1" -d "gtune[devil.cooldown]=" $B/features
fi
CSRF=$(grep -o "name=['\"]\?csrf['\"]\? value='[^']*'" /tmp/d1.html | head -1 | sed "s/.*value='\([^']*\)'.*/\1/")
echo "  csrf: ${CSRF:0:12}…"
BEFORE=$(python3 -c "
import json;d=json.load(open('$CFG',encoding='utf-8'));print(json.dumps(d.get('tuning') or {},ensure_ascii=False))")
echo "  保存前 tuning = $BEFORE"
curl -s -b $J -o /tmp/d2.html -w '  保存 -> %{http_code}\n' \
  --data-urlencode "csrf=$CSRF" -d "feat_form=1" -d "gtune[devil.cooldown]=601" $B/features
grep -q "已保存" /tmp/d2.html && echo "  页面报「已保存」✓" || { echo "  !! 没保存成功"; grep -o "msg bad'>[^<]*" /tmp/d2.html | head -2; FAIL=1; }
grep -o "本次改动[^<]*" /tmp/d2.html | head -1
python3 - "$CFG" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print("  保存后 tuning =", json.dumps(d.get("tuning") or {}, ensure_ascii=False))
ok = (d.get("tuning") or {}).get("devil.cooldown") == 601
print("  ✓ 值已落盘" if ok else "  !! 值没落盘")
raise SystemExit(0 if ok else 1)
PY
[ $? -eq 0 ] || FAIL=1
echo -n "  审计最后一行: "; tail -1 $AUDIT | cut -c1-120

echo
echo "=== 5) 越界值要被拒（devil.base_prob = 5） ==="
curl -s -b $J -o /tmp/d3.html -w '  保存 -> %{http_code}\n' \
  --data-urlencode "csrf=$CSRF" -d "feat_form=1" -d "gtune[devil.base_prob]=5" $B/features
grep -q "未写入" /tmp/d3.html && echo "  页面报「未写入」✓" || { echo "  !! 越界没被拒"; FAIL=1; }

echo
echo "=== 6) 重启让它生效 ==="
cd /opt/dzmm && docker compose restart dzmm-bot >/dev/null 2>&1
sleep 14
docker logs --tail 60 dzmm-bot 2>&1 | grep -e "P12:" | sed 's/^/  /'

echo
echo "=== 7) 撤销覆盖（留空 = 回到默认） ==="
# ⚠️ 第 6 步重启会把后台会话清掉（会话只存在内存里）⇒ 必须重新登录，
# 否则这一步的 POST 只是拿回一个登录页（200），看着"成功"其实什么都没写。
curl -s -o /dev/null -c $J -d "username=$USER" -d "password=$PW" $B/login
curl -s -b $J -o /tmp/d4.html $B/features
CSRF2=$(grep -o "name=['\"]\?csrf['\"]\? value='[^']*'" /tmp/d4.html | head -1 | sed "s/.*value='\([^']*\)'.*/\1/")
grep -q "feat_form" /tmp/d4.html || { echo "  !! 重登后页面异常"; FAIL=1; }
curl -s -b $J -o /tmp/d5.html -w '  保存 -> %{http_code}\n' \
  --data-urlencode "csrf=$CSRF2" -d "feat_form=1" -d "gtune[devil.cooldown]=" $B/features
grep -q "已保存" /tmp/d5.html && echo "  页面报「已保存」✓" || { echo "  !! 撤销没成功"; grep -o "msg bad'>[^<]*" /tmp/d5.html | head -2; FAIL=1; }
python3 - "$CFG" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
t = d.get("tuning") or {}
print("  撤销后 tuning =", json.dumps(t, ensure_ascii=False))
print("  ✓ 已回到「没有覆盖」" if not t else "  !! 还有残留")
raise SystemExit(0 if not t else 1)
PY
[ $? -eq 0 ] || FAIL=1

cd /opt/dzmm && docker compose restart dzmm-bot >/dev/null 2>&1
sleep 14
echo
echo "=== 8) 最终状态 ==="
docker logs --tail 60 dzmm-bot 2>&1 | grep -e "P12:" -e "P11: 配置=" -e "start  dry" | sed 's/^/  /'
echo -n "  异常命中: "
docker logs --tail 300 dzmm-bot 2>&1 | grep -ciE 'traceback|exception'
echo
[ $FAIL -eq 0 ] && echo "===> 功能与参数页验收：全部通过 ✓" || echo "===> 功能与参数页验收：**有失败项** ✗"
rm -f $J
exit $FAIL
