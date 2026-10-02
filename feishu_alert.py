"""飞书自定义机器人 webhook 告警（无 session、POST 即达，取代微信告警主通道）。

用法：
    from feishu_alert import send_feishu
    send_feishu("🚨 告警内容")

配置（环境变量）：
    FEISHU_WEBHOOK  https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
    FEISHU_SECRET   可选，签名校验密钥（群机器人安全设置里选「签名校验」后复制）

成功判据：HTTP 200 且响应体 code == 0。
"""
import json
import os
import time
import hmac
import hashlib
import base64
import requests

# 从 .env 文件加载（宿主机 shell 可能未设置，容器 docker-compose 会传入）
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
SECRET = os.environ.get("FEISHU_SECRET", "")


def _gen_sign(timestamp: int) -> str:
    """飞书签名：key = timestamp+"\\n"+secret，HMAC-SHA256 空消息，base64。"""
    string_to_sign = f"{timestamp}\n{SECRET}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"),
                         digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_feishu(text: str, retries: int = 2) -> bool:
    """发一条飞书群消息。失败时返回 False 并打印原因到 stderr。"""
    import sys
    if not text or not text.strip() or not WEBHOOK:
        print(f"[feishu] skip: webhook_empty={not WEBHOOK} text_empty={not text}", file=sys.stderr)
        return False
    msg = {"msg_type": "text", "content": {"text": text}}
    if SECRET:
        timestamp = str(int(time.time()))
        msg["timestamp"] = timestamp
        msg["sign"] = _gen_sign(int(timestamp))
    body = json.dumps(msg, ensure_ascii=False)
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(WEBHOOK, data=body.encode("utf-8"),
                              headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200:
                try:
                    resp = r.json()
                    if resp.get("code") == 0:
                        return True
                    last_err = f"code={resp.get('code')} msg={resp.get('msg', '')[:40]}"
                except Exception as e:
                    last_err = f"json_parse: {e}"
            else:
                last_err = f"http_{r.status_code}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:40]}"
        if attempt < retries:
            time.sleep(3 * (attempt + 1))
    # P12-BR-fix: 所有重试失败后记录原因（原来静默返回 False，完全无法排查）
    print(f"[feishu] send failed after {retries+1} attempts: {last_err}", file=sys.stderr)
    return False


if __name__ == "__main__":
    ok = send_feishu("🤖 [飞书告警自测] 通道正常")
    print("发送结果:", "✅ 成功" if ok else "❌ 失败")
