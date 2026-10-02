#!/usr/bin/env python3
"""诊断：谁持有 /app/.session.lock 的 flock？（只读，不改状态）

背景：flock 模式下锁文件残留是正常的（内核在进程退出时释放锁，文件故意不删）。
所以"文件存在"不等于"锁被持有"。真正要查的是**哪个进程还开着它**。

已知的可疑路径：renew_token 拿锁后 `subprocess.Popen(headless_relogin.py)`，
**子进程会继承锁的 fd** —— flock 绑在 open file description 上，
只要还有一个继承者活着，锁就一直被持有 ⇒ 后续全部 SKIP。
"""
import fcntl
import os
import sys

LOCK = "/app/.session.lock"


def try_acquire():
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True, None
    except OSError as e:
        return False, e
    finally:
        os.close(fd)


def holders():
    """遍历 /proc 找哪些进程开着这个 inode。"""
    try:
        ino = os.stat(LOCK).st_ino
    except Exception as e:
        print("  stat 失败:", e)
        return []
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fddir = "/proc/{}/fd".format(pid)
        try:
            for fd in os.listdir(fddir):
                try:
                    if os.stat(os.path.join(fddir, fd)).st_ino == ino:
                        cmd = open("/proc/{}/cmdline".format(pid), "rb").read()
                        cmd = cmd.replace(b"\0", b" ").decode("utf-8", "replace")[:80]
                        out.append((pid, fd, cmd))
                except Exception:
                    pass
        except Exception:
            pass
    return out


print("锁文件:", LOCK, "存在 =", os.path.exists(LOCK))
try:
    print("内容:", open(LOCK).read().strip())
except Exception as e:
    print("读内容失败:", e)

free, err = try_acquire()
print("\n能否立刻拿到 flock =", free, "" if free else "(被占用: {})".format(err))

hs = holders()
print("\n打开该文件的进程（{} 个）:".format(len(hs)))
for pid, fd, cmd in hs:
    print("  pid={} fd={}  {}".format(pid, fd, cmd))
if not hs:
    print("  （没有）—— 说明锁没被持有，SKIP 一定另有原因")

print("\n当前 renew/relogin 相关进程:")
found = []
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        cmd = open("/proc/{}/cmdline".format(pid), "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
        if any(k in cmd for k in ("renew_token", "headless_relogin", "bot.py")):
            found.append("  pid={} {}".format(pid, cmd[:100]))
    except Exception:
        pass
print("\n".join(found) or "  （无）")
sys.exit(0)
