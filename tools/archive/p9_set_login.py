"""在 /opt/dzmm/.env 里幂等地设置运维后台的**账号密码**登录。

- 已存在就**保持原值**（不要每次部署都换密码）
- 随机密码刻意排除容易看错的字符（0/O/1/l/I），因为应急时可能要手打
- 顺手清掉已经废弃的 DZMM_ADMIN_TOKEN（换成登录之后它没有用了，
  留着是个没人管的密钥）
"""
import random
import secrets
import sys
from pathlib import Path

ENV = Path("/opt/dzmm/.env")
USER_KEY = "DZMM_ADMIN_USER"
PW_KEY = "DZMM_ADMIN_PASSWORD"
DEAD_KEY = "DZMM_ADMIN_TOKEN"

# 去掉 0 O o 1 l I 这几个容易混淆的
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_password(n=14):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def main() -> int:
    if not ENV.exists():
        print("找不到 {}".format(ENV))
        return 1
    # 一律用 LF 写：CRLF 与 LF 混用会让 shell 读到的值和 docker compose
    # 读到的值不同（compose 会去掉值尾部的 CR，cut 不会）—— 这个坑踩过一次。
    raw = ENV.read_bytes().decode("utf-8").replace("\r\n", "\n")
    eol = "\n"
    lines = raw.split(eol)

    def current(key):
        for ln in lines:
            if ln.strip().startswith(key + "="):
                return ln.split("=", 1)[1].strip()
        return ""

    user = current(USER_KEY) or "admin"
    pw = current(PW_KEY)
    removed = [ln for ln in lines if ln.strip().startswith(DEAD_KEY + "=")]

    if pw:
        print("账号密码已存在，保持原值：账号={} 密码长度={}".format(user, len(pw)))
    else:
        pw = gen_password()
        out = [ln for ln in lines if not ln.strip().startswith(DEAD_KEY + "=")]
        out = [ln for ln in out if not ln.strip().startswith(PW_KEY + "=")]
        out = [ln for ln in out if not ln.strip().startswith(USER_KEY + "=")]
        if out and out[-1].strip() != "":
            out.append("")
        out.append("# 运维后台登录（P9）。声明对外发布时必填，密码 <8 位会被拒绝启动。")
        out.append("{}={}".format(USER_KEY, user))
        out.append("{}={}".format(PW_KEY, pw))
        ENV.write_bytes(eol.join(out).encode("utf-8"))
        print("已写入：账号={}  密码={}".format(user, pw))
        if removed:
            print("顺手清掉了 {} 行已废弃的 {}".format(len(removed), DEAD_KEY))
    return 0


if __name__ == "__main__":
    sys.exit(main())
