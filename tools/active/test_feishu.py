#!/usr/bin/env python3
import sys
sys.path.insert(0, "/app")
from feishu_alert import send_feishu
ok = send_feishu("[test] feishu alert channel check")
print("result:", ok)
