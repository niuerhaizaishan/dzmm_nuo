#!/bin/sh
# 定性：22:30/22:31 那两批问候到底是哪个机制发的，以及非认证群今晚还会不会再收到
echo "=== 1) 日志里的 greet 行（这是问候机制自己的自证） ==="
docker logs --tail 4000 dzmm-bot 2>&1 | grep -e 'greet\[' | tail -30
echo
echo "=== 2) 宿主机 crontab（看有没有 --greet 定时任务） ==="
crontab -l 2>&1 | grep -v '^#'
echo
echo "=== 3) entrypoint 里的定时器 ==="
grep -n -e greet -e crontab -e while -e sleep /opt/dzmm/entrypoint.sh 2>/dev/null | head -20
echo
echo "=== 4) 每个群的 last_greet_evening 打点（今天=2026-09-25） ==="
docker exec -i dzmm-bot python - <<'PY'
import sys, json
sys.path.insert(0, '/app')
import bot
st = bot.load_state()
alias = {
    'bd7fe6a4-0065-4551-ad3d-94116dba8f9c': '认证妹子群(有Bot)',
    '93cc6c7b-ba9e-48f3-a106-3074483aebbd': '裳姐姐和她的骚贱小母狗',
    '38698306-4252-438a-aa56-7dbab89250eb': '被dzmm团本折磨的三人',
    '007bfd75-2efb-491c-8233-20046af69b24': '机电驱动与智能算法研讨群',
    '4a56be71-9ca6-4785-b45f-18725d187f1a': 'Rose（505）玫瑰房',
}
print("  PUSH_CIDS(当前生效) =",
      [alias.get(c, c[:8]) for c in bot.PUSH_CIDS])
for c in alias:
    d = st.get(c) or {}
    print("  {:<26s} last_greet_morning={:<12s} noon={:<12s} evening={:<12s} memreport={}".format(
        alias[c], str(d.get('last_greet_morning')), str(d.get('last_greet_noon')),
        str(d.get('last_greet_evening')), str(d.get('last_memreport'))))
PY
