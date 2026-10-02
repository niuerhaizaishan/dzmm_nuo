#!/usr/bin/env python3
import os
w = os.environ.get("FEISHU_WEBHOOK", "")
s = os.environ.get("FEISHU_SECRET", "")
print("WEBHOOK:", w[:50] + "..." if len(w) > 50 else (w if w else "EMPTY"))
print("SECRET:", "set" if s else "EMPTY")
print("WEBHOOK_LEN:", len(w))
