#!/bin/sh
# P12-L：等"自我介绍配图"真正发出去（日志出现「自我介绍配图」），再回读群里最后几条。
# 用法：sh /tmp/watch_intro_photo.sh [最多等几轮]（每轮 7 秒）
LOG=/opt/dzmm/bot.log
ROUNDS=${1:-40}
n0=$(grep -ac '自我介绍配图' $LOG 2>/dev/null || echo 0)
echo "基线：配图日志行数=$n0"
i=0
while [ $i -lt $ROUNDS ]; do
  n1=$(grep -ac '自我介绍配图' $LOG 2>/dev/null || echo 0)
  if [ "$n1" -gt "$n0" ]; then break; fi
  sleep 7
  i=$((i + 1))
done
echo "--- 配图相关日志（最近 4 条）---"
grep -a '配图' $LOG | tail -4
echo "--- 最近房间消息 ---"
grep -a '房间消息' $LOG | tail -3
echo "--- 回读群消息（含类型）---"
docker cp /tmp/watch_rose_card.py dzmm-bot:/tmp/watch_rose_card.py >/dev/null 2>&1 \
  && docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python /tmp/watch_rose_card.py 0 5
