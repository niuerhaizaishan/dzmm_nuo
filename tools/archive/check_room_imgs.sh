#!/bin/sh
# 校验四张布局图的 md5 与公网可访问性
cd /opt/dzmm/deploy/img/room || exit 1
echo "--- 服务器上的 md5 ---"
md5sum *.jpg
echo "--- 公网回读 ---"
for f in rose pool bbq shenwenshi; do
  out=$(curl -s -o /tmp/x_$f.jpg -w '%{http_code} %{size_download}' \
        "https://www.nuonuo.pw/img/room/$f.jpg")
  echo "$f.jpg -> $out  远端md5=$(md5sum /tmp/x_$f.jpg | cut -d' ' -f1)"
done
