#!/bin/sh
# P12-M：盯"@ 消息之后有没有刷屏"。
# 基线放回次数 = 0；一旦出现新的「同批还有」就是 bug 复发；
# 检测到新的 @消息 后等 45 秒，数这 45 秒里机器人发了几条（正常 1~2 条）。
LOG=/opt/dzmm/bot.log
R0=$(grep -ac '同批还有' $LOG 2>/dev/null || echo 0)
A0=$(grep -ac '@消息' $LOG 2>/dev/null || echo 0)
C0=$(grep -ac '💬' $LOG 2>/dev/null || echo 0)
echo "基线：放回=$R0  @消息=$A0  机器人发言=$C0"
i=0
hit=""
while [ $i -lt 40 ]; do
  R1=$(grep -ac '同批还有' $LOG 2>/dev/null || echo 0)
  A1=$(grep -ac '@消息' $LOG 2>/dev/null || echo 0)
  if [ "$R1" -gt "$R0" ]; then hit="LOOP"; break; fi
  if [ "$A1" -gt "$A0" ]; then hit="AT"; break; fi
  sleep 7
  i=$((i + 1))
done
if [ -z "$hit" ]; then
  echo "等满窗口：没有新的 @消息，也没有新的放回。"
else
  echo "命中类型：$hit"
  if [ "$hit" = "AT" ]; then
    sleep 45
    C1=$(grep -ac '💬' $LOG 2>/dev/null || echo 0)
    R1=$(grep -ac '同批还有' $LOG 2>/dev/null || echo 0)
    echo "@ 之后 45 秒内：机器人发言 $((C1 - C0)) 条（正常 1~2 条），新增放回 $((R1 - R0)) 次（正常 0）"
  fi
fi
echo "--- 最近 @消息 ---"
grep -a '@消息' $LOG | tail -2
echo "--- 最近放回 ---"
grep -a '同批还有' $LOG | tail -2
echo "--- 回读群消息 ---"
docker cp /tmp/watch_rose_card.py dzmm-bot:/tmp/watch_rose_card.py >/dev/null 2>&1 \
  && docker exec -e PYTHONIOENCODING=utf-8 dzmm-bot python /tmp/watch_rose_card.py 0 5
