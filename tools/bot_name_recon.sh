#!/bin/sh
# 当前生产上的 Bot 名字配置
echo "=== /opt/dzmm/.env 里的相关键 ==="
grep -n -E 'BOT_NAME|BOT_SHORT|BOT_ID|BOT_CID' /opt/dzmm/.env 2>/dev/null || echo "  .env 里一个都没设 -> 走 compose 默认值"

echo
echo "=== 容器里实际生效的值 ==="
docker exec dzmm-bot sh -c 'echo "  DZMM_BOT_NAME  = [$DZMM_BOT_NAME]"; echo "  DZMM_BOT_SHORT = [$DZMM_BOT_SHORT]"; echo "  DZMM_BOT_ID    = [$DZMM_BOT_ID]"'

echo
echo "=== 侧车账号在成员表里的显示名（对比用） ==="
docker exec dzmm-bot sh -c 'cd /app && python -c "
import sys; sys.path.insert(0,\".\")
from dzmm import get_chatroom_name
import state_store, runtime.ctx as ctx
ctx.set_active_cid(\"bd7fe6a4-0065-4551-ad3d-94116dba8f9c\")
import runtime.identity as ident
print(\"  侧车完整名:\", ident.get_full_name(ident.NUO_UID)[:40])
print(\"  侧车简化名:\", ident.get_name(ident.NUO_UID)[:40])
print(\"  self_names():\", sorted(ident.self_names()))
"' 2>&1 | tail -6
