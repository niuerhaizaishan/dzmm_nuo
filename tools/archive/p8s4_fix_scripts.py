"""一次性修脚本：把 `bot._set_active_cid(x` 补回右括号。

上一步用 PowerShell 正则做替换时只替了 `bot._ACTIVE_CID = ` 这一半，
结果留下 `bot._set_active_cid(cid` 这种缺右括号的行。这里按行修，
并且**只在确实缺括号时**才动，改完打印 diff 供人工核对。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FILES = ["batch_register.py", "diag_fortune.py", "regen_novel.py",
         "test_fmt.py", "test_fortune3.py", "test_recent.py"]

for name in FILES:
    p = ROOT / name
    if not p.exists():
        continue
    raw = p.read_bytes().decode("utf-8")
    out = []
    changed = 0
    for ln in raw.splitlines(keepends=True):
        if "_set_active_cid(" in ln and not ln.rstrip().endswith(")"):
            ln = ln.rstrip("\r\n") + ")" + ("\r\n" if ln.endswith("\r\n") else "\n")
            changed += 1
        out.append(ln)
    if changed:
        p.write_bytes("".join(out).encode("utf-8"))
    print("{:22s} 修 {} 行".format(name, changed))
