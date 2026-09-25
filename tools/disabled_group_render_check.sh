#!/bin/sh
# 用户禁用 4 个群之后，配置页还渲染得出来吗？被禁用的群会消失吗？
PW=$(grep -E '^DZMM_ADMIN_PASSWORD=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
USER=$(grep -E '^DZMM_ADMIN_USER=' /opt/dzmm/.env | cut -d= -f2- | tr -d '\r')
J=/tmp/x_cookies.txt
rm -f $J
curl -s -o /dev/null -c $J -d "username=$USER" -d "password=$PW" http://127.0.0.1:8090/login
curl -s -b $J -o /tmp/x_edit.html http://127.0.0.1:8090/edit
echo "=== 页面上群表有多少行 / 出现哪些 cid ==="
echo -n "  groups[ 字段个数: "
grep -o "name='groups\[[0-9]*\]\[cid\]'" /tmp/x_edit.html | sort -u | wc -l
grep -o "name='groups\[[0-9]*\]\[cid\]' value='[^']*'" /tmp/x_edit.html | sed 's/.*value=//'
echo
echo "=== 页面上有没有出现那 4 个被禁用的群 id ==="
for c in 93cc6c7b 38698306 007bfd75 4a56be71; do
  n=$(grep -c "$c" /tmp/x_edit.html)
  echo "  $c 在页面里出现 $n 次"
done
echo
echo "=== 页面上有没有"尚未生效"提示（文件比进程新） ==="
grep -q "尚未生效" /tmp/x_edit.html && echo "  有 ✓" || echo "  没有 !!（文件 22:41 改的，进程 22:37 启动，应该报）"
grep -o "有改动尚未生效[^<]*" /tmp/x_edit.html | head -1
echo
echo "=== 页面上的改动清单里写了什么（上次保存的反馈已过期，这里是当前页） ==="
grep -o "本次改动[^<]*" /tmp/x_edit.html | head -2
echo
echo "=== 用一次性的"什么都不改"提交试试：看它会不会把 4 个群当"新增" ==="
python3 - <<'PY' > /tmp/x_form.txt
import re, urllib.parse
html = open('/tmp/x_edit.html', encoding='utf-8').read()
csrf = re.search(r"name=['\"]?csrf['\"]? value='([^']*)'", html).group(1)
m = re.search(r"<form[^>]*action='/edit'[^>]*>(.*?)</form>", html, re.S)
out = []
for tag in re.findall(r"<input\b[^>]*>", m.group(1)):
    nm = re.search(r"name=['\"]?([^'\"\s>]+)", tag)
    if not nm:
        continue
    k = nm.group(1)
    if re.search(r"type=['\"]?checkbox", tag) and not re.search(r"\bchecked\b", tag):
        continue
    vm = re.search(r"value='([^']*)'", tag)
    out.append((k, csrf if k == 'csrf' else (vm.group(1) if vm else "")))
print(urllib.parse.urlencode(out))
PY
curl -s -b $J -o /tmp/x_save.html -w "  提交 -> %{http_code}\n" --data-binary @/tmp/x_form.txt http://127.0.0.1:8090/edit
grep -o "msg ok'>[^<]*" /tmp/x_save.html | head -1
grep -o "本次改动[^<]*" /tmp/x_save.html | head -1
grep -o "<li class='mono'>[^<]*" /tmp/x_save.html | head -6
echo
echo "=== 提交后文件里的群数（会不会被写丢） ==="
python3 -c "
import json
c = json.load(open('/opt/dzmm/config/config.json', encoding='utf-8'))
print('  文件里群数:', len(c['groups']))
for g in c['groups']:
    print('   ', g['cid'][:8], 'enabled=', g['enabled'])
"
