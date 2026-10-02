"""把 /opt/dzmm/.env 的行尾统一成 LF，并验证**值一个都没变**。

起因：这个文件里 CRLF 与 LF 混用，而
  * `docker compose` 读 .env 时会**自动去掉** 值尾部的 `\\r`
  * shell 的 `cut -d= -f2-` **不会**
于是"同一条配置"在容器里和在脚本里是两个不同的字符串 —— 排查登录 401 时
就栽在这上面（服务端日志打出 `账号='admin\\r'`）。

统一成 LF，两边就一致了。改完逐项核对：每个 KEY 的值（去掉 CR 后）必须与改之前完全相同。
"""
import sys
from pathlib import Path

ENV = Path("/opt/dzmm/.env")


def parse(raw):
    out = {}
    for ln in raw.replace("\r\n", "\n").split("\n"):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main() -> int:
    if not ENV.exists():
        print("找不到", ENV)
        return 1
    raw = ENV.read_bytes().decode("utf-8")
    before = parse(raw)
    crlf = raw.count("\r\n")
    total = raw.count("\n")
    print("改前：总行 {} / CRLF {} / 纯 LF {}".format(total, crlf, total - crlf))

    if crlf == 0:
        print("已经是纯 LF，不需要改")
        return 0

    backup = ENV.with_suffix(".env.bak-eol")
    backup.write_bytes(ENV.read_bytes())
    new = raw.replace("\r\n", "\n")
    ENV.write_bytes(new.encode("utf-8"))

    after = parse(new)
    if before != after:
        ENV.write_bytes(raw.encode("utf-8"))
        print("!! 值发生了变化，已回滚。差异：")
        for k in set(before) | set(after):
            if before.get(k) != after.get(k):
                print("   {}: {!r} -> {!r}".format(k, before.get(k), after.get(k)))
        return 1

    print("改后：总行 {} / CRLF {} / 纯 LF {}".format(
        new.count("\n"), new.count("\r\n"), new.count("\n") - new.count("\r\n")))
    print("逐项核对：{} 个键的取值**完全一致**".format(len(after)))
    print("备份留在 {}（确认没问题后可删）".format(backup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
