#!/bin/sh
# 改名收尾：续期健康 + 清理容器里的临时脚本
echo "=== 1) 续期日志（3 次重启后有无异常） ==="
tail -8 /opt/dzmm/renew_token.log | sed 's/^/  /'
echo -n "  有没有被会话锁挡过（应为 0）: "
grep -c '另一个进程正在' /opt/dzmm/renew_token.log 2>/dev/null || echo 0
# ⚠️ 不能对整个文件 grep -c FAIL —— 那是历史累积（09-23 那次验证码事件就 57 条），
# 数出来 173 会把人吓一跳。要看的是"最近有没有 FAIL"。
echo -n "  最近 30 行里有几条 FAIL（应为 0）: "
tail -30 /opt/dzmm/renew_token.log | grep -c 'FAIL' || true
echo -n "  今日全天 FAIL 条数（历史常态 1-6 条，会自愈）: "
grep -c "^$(date +%Y-%m-%d).*FAIL" /opt/dzmm/renew_token.log 2>/dev/null || echo 0
echo "  最近一条 FAIL: $(grep 'FAIL' /opt/dzmm/renew_token.log | tail -1 | cut -c1-19)"

echo
echo "=== 2) token 到期时间 ==="
docker exec -i dzmm-bot python - <<'PY'
import json, time
from datetime import datetime
try:
    d = json.load(open("/app/.dzmm_token.json"))
    for c in d.get("cookies", []):
        if c.get("name") == "sb-rls-auth-token":
            exp = c.get("expires")
            print("  auth-token 到期:", datetime.fromtimestamp(float(exp)).strftime("%m-%d %H:%M:%S"),
                  "| 剩余 {:.0f} 小时".format((float(exp) - time.time()) / 3600))
except Exception as e:
    print("  读不到 token:", e)
PY

echo
echo "=== 3) 容器里的临时排查脚本（清掉） ==="
docker exec dzmm-bot sh -c 'rm -f /app/tools_read_bot_name.py /app/tools_read_bot2.py /app/tools_probe_at.py; ls /app/tools_* 2>/dev/null || echo "  已清空"'

echo
echo "=== 4) 最终状态 ==="
docker ps --format '  {{.Names}}  {{.Status}}  {{.Ports}}'
docker inspect -f '  restarts={{.RestartCount}}' dzmm-bot
docker stats --no-stream dzmm-bot --format '  CPU={{.CPUPerc}} MEM={{.MemUsage}}'
docker logs --tail 20 dzmm-bot 2>&1 | grep -E 'bot路由|P8:|心跳|sender ready' | sed 's/^/  /'
echo -n "  异常命中: "
docker logs --tail 200 dzmm-bot 2>&1 | grep -icE 'traceback|exception' || true
