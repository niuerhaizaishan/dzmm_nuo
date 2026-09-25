"""在 /opt/dzmm/.env 里幂等地写入一个随机的 DZMM_ADMIN_TOKEN。

- 已经设过就**保持原值**（不要每次部署都换令牌，否则书签会失效）
- 保留原文件的行尾与其它内容，只动这一行
- 顺手打印一下掩码后的值，便于确认"确实设了"
"""
import secrets
import sys
from pathlib import Path

ENV = Path("/opt/dzmm/.env")
KEY = "DZMM_ADMIN_TOKEN"


def main() -> int:
    if not ENV.exists():
        print("找不到 {}".format(ENV))
        return 1
    raw = ENV.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(eol)

    for ln in lines:
        if ln.strip().startswith(KEY + "="):
            val = ln.split("=", 1)[1].strip()
            if val:
                print("已存在，保持原值：{}…（长度 {}）".format(val[:6], len(val)))
                return 0

    token = secrets.token_urlsafe(24)
    out = [ln for ln in lines if not ln.strip().startswith(KEY + "=")]
    if out and out[-1].strip() != "":
        out.append("")
    out.append("# 运维后台令牌（P9）。面板对外发布时必须设置，否则 dashboard 拒绝启动。")
    out.append("{}={}".format(KEY, token))
    ENV.write_bytes(eol.join(out).encode("utf-8"))
    print("已写入新令牌：{}…（长度 {}）".format(token[:6], len(token)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
