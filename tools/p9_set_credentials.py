"""设置运维后台的登录账号密码（幂等覆盖）。

    python3 tools/p9_set_credentials.py <账号> <密码>

- 直接覆盖 .env 里的 DZMM_ADMIN_USER / DZMM_ADMIN_PASSWORD
- 一律用 LF 写（CRLF/LF 混用会让 shell 读到的值和 docker compose 读到的不一样 ——
  这个坑踩过一次，见 docs/小小糯总方案.md）
- 改完逐项回读确认，并把密码按长度打码输出
- **不动 .env 的其它任何行**
"""
import sys
from pathlib import Path

ENV = Path("/opt/dzmm/.env")
USER_KEY = "DZMM_ADMIN_USER"
PW_KEY = "DZMM_ADMIN_PASSWORD"


def read_env():
    raw = ENV.read_bytes().decode("utf-8").replace("\r\n", "\n")
    lines = raw.split("\n")
    values = {}
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            values[k.strip()] = v.strip()
    return lines, values


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    user, pw = argv[1], argv[2]
    if not ENV.exists():
        print("找不到", ENV)
        return 1
    if not user or "=" in user or "\n" in user:
        print("账号不合法")
        return 1
    if len(pw) < 8:
        print("密码太短（<8 位），dashboard 会拒绝启动")
        return 1

    lines, before = read_env()
    out = [ln for ln in lines
           if not ln.strip().startswith(USER_KEY + "=")
           and not ln.strip().startswith(PW_KEY + "=")]
    # 去掉尾部多余空行，再补一行空行 + 注释 + 两行配置
    while out and out[-1].strip() == "":
        out.pop()
    out.append("")
    out.append("# 运维后台登录（P9）。密码 <8 位会被拒绝启动。")
    out.append("{}={}".format(USER_KEY, user))
    out.append("{}={}".format(PW_KEY, pw))
    ENV.write_bytes(("\n".join(out) + "\n").encode("utf-8"))

    _, after = read_env()
    ok_user = after.get(USER_KEY) == user
    ok_pw = after.get(PW_KEY) == pw
    others_changed = sorted(
        k for k in set(before) | set(after)
        if k not in (USER_KEY, PW_KEY) and before.get(k) != after.get(k))
    print("账号 {} 写入={}".format(user, "OK" if ok_user else "**失败**"))
    print("密码 写入={}（长度 {}，前 2 位 {}）".format(
        "OK" if ok_pw else "**失败**", len(pw), pw[:2]))
    print("其它键是否被改动：{}".format(others_changed or "无"))
    print("行尾：CRLF {} 行 / 纯 LF {} 行".format(
        ENV.read_bytes().count(b"\r\n"), ENV.read_bytes().count(b"\n")))
    return 0 if (ok_user and ok_pw and not others_changed) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
