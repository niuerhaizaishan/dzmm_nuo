"""通用：设置 /opt/dzmm/.env 里的若干个键（幂等覆盖）。

    python3 tools/env_set.py KEY=VALUE [KEY=VALUE ...]

- 一律用 LF 写（CRLF/LF 混用会让 shell 读到的值和 docker compose 读到的不一样）
- 改完**逐项回读**，并列出"其它键是否被动过"
- 已存在的键就地替换（保持原来那一行的位置），不存在才追加到末尾
- 值的首尾空格会被去掉（.env 的既有约定）
"""
import sys
from pathlib import Path

ENV = Path("/opt/dzmm/.env")


def parse(raw):
    out = {}
    for ln in raw.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pairs = []
    for a in argv[1:]:
        if "=" not in a:
            print("参数格式应为 KEY=VALUE：", a)
            return 2
        k, v = a.split("=", 1)
        pairs.append((k.strip(), v.strip()))
    if not ENV.exists():
        print("找不到", ENV)
        return 1

    raw = ENV.read_bytes().decode("utf-8").replace("\r\n", "\n")
    before = parse(raw)
    lines = raw.split("\n")
    keys = {k for k, _ in pairs}

    for k, v in pairs:
        done = False
        for i, ln in enumerate(lines):
            if ln.strip().startswith(k + "="):
                lines[i] = "{}={}".format(k, v)
                done = True
                break
        if not done:
            if lines and lines[-1].strip() != "":
                lines.append("")
            lines.append("{}={}".format(k, v))

    ENV.write_bytes(("\n".join(lines).rstrip("\n") + "\n").encode("utf-8"))

    after = parse(ENV.read_bytes().decode("utf-8"))
    ok = all(after.get(k) == v for k, v in pairs)
    others = sorted(k for k in set(before) | set(after)
                    if k not in keys and before.get(k) != after.get(k))
    for k, v in pairs:
        got = after.get(k)
        print("  {} = {!r}  {}".format(k, got, "OK" if got == v else "**写失败**"))
    print("  其它键是否被动过：{}".format(others or "无"))
    b = ENV.read_bytes()
    print("  行尾：CRLF {} / 纯 LF {}".format(b.count(b"\r\n"), b.count(b"\n")))
    return 0 if (ok and not others) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
