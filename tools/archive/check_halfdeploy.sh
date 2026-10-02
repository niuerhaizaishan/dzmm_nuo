#!/bin/sh
# 半更新状态排查：服务器上的代码、新模块、运行中的进程
echo "--- bot.py 里的新钩子 ---"
grep -c '_room_layout' /opt/dzmm/bot.py
grep -c '_intro_photo' /opt/dzmm/bot.py
ls -la /opt/dzmm/bot.py
echo "--- 新模块在不在 ---"
ls -la /opt/dzmm/runtime/room_layout.py /opt/dzmm/runtime/bot_photo.py 2>&1 | tail -3
echo "--- 容器内同样两份 ---"
docker exec dzmm-bot ls -la /app/runtime/room_layout.py /app/runtime/bot_photo.py 2>&1 | tail -3
echo "--- 容器状态 ---"
docker ps --format '{{.Names}} {{.Status}}'
echo "--- 容器能否 import bot ---"
docker exec dzmm-bot python -c "import bot; print('bot import OK')" 2>&1 | tail -3
echo "--- 最近启动行 ---"
tail -5 /opt/dzmm/bot.log
