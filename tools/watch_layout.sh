#!/bin/sh
# P12-N 冒烟监看：等"配图已发"，然后回读群里最后几条
LOG=/opt/dzmm/bot.log
N0=$(grep -ac '配图已发' $LOG 2>/dev/null || echo 0)
echo "基线：配图日志=$N0"
i=0
while [ $i -lt 40 ]; do
  N1=$(grep -ac '配图已发' $LOG 2>/dev/null || echo 0)
  if [ "$N1" -gt "$N0" ]; then break; fi
  sleep 7
  i=$((i + 1))
done
echo "--- 配图 / 放回 日志 ---"
grep -a '配图' $LOG | tail -3
grep -a '同批还有' $LOG | tail -1
echo "--- 最近房间消息 ---"
grep -a '房间消息' $LOG | tail -4
echo "--- 回读群消息 ---"
docker cp /tmp/watch_rose_card.py dzmm-bot:/tmp/watch_rose_card.py >/dev/null 2>&1 \
  && docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python /tmp/watch_rose_card.py 0 4
