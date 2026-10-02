"""在新群里找「工具熊」：昵称/uid/性别（只读）。"""
import sys

sys.path.insert(0, "/app")

from dzmm import get_members                     # noqa: E402
from runtime.members import _gender_of           # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
ms = get_members(CID) or {}
print("群成员数：", len(ms))
hit = [(u, n) for u, n in ms.items() if "工具" in str(n) or "熊" in str(n)]
print("名字里带「工具」或「熊」的：")
for u, n in hit:
    print("  uid={}  昵称={!r}  性别={}".format(u, n, _gender_of(u, CID)))
if not hit:
    print("  （没有命中）")
    print("  全部成员昵称（前 60 个）：")
    for u, n in list(ms.items())[:60]:
        print("    {!r}".format(n))
