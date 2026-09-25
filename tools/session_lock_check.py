"""Linux 侧会话锁验证（在目标镜像内运行）。

为什么单独写：本机是 Windows，`fcntl` 不存在 → flock 后端在本地**全部跳过**，
而生产用的正是 flock。所以必须在 Linux 上真跑一遍，否则等于没验证。

    docker run --rm --entrypoint python -e PYTHONPATH=/app \
        -v /opt/dzmm:/app:ro -v /tmp/session_lock_check.py:/chk.py:ro \
        -w /tmp dzmm-bot:latest /chk.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")

from runtime import session  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print("  [{}] {}{}".format("OK" if ok else "!!", name,
                               (" -- " + str(detail)) if detail else ""))
    if not ok:
        fails.append(name)


tmp = Path(tempfile.mkdtemp(prefix="lockchk-"))
session.LOCK_FILE = tmp / ".session.lock"
session.VERSION_FILE = tmp / ".session_version.json"
session.FORCED_BACKEND = None

print("backend:", "flock" if session._fcntl is not None else "excl",
      "(fcntl ok: {})".format(session._fcntl is not None))
print()
check("Linux 上应有 fcntl", session._fcntl is not None)

# 1) 空闲时可取
with session.guard("first", timeout=1.0) as got:
    check("空闲时可取锁", got is True)

# 2) 持锁期间互斥 —— 这是整个 P5 的核心
with session.guard("first", timeout=1.0) as got1:
    check("再次可取（上一次已释放）", got1 is True)
    with session.guard("second", timeout=0.3) as got2:
        check("持锁期间第二次被拒（互斥）", got2 is False)
    with session.guard("third", timeout=0.3) as got3:
        check("持锁期间第三次仍被拒", got3 is False)
# 离开 with 之后才应释放
with session.guard("after-release", timeout=1.0) as got4:
    check("释放后可取", got4 is True)

# 3) 异常路径也释放
try:
    with session.guard("boom", timeout=1.0):
        raise RuntimeError("x")
except RuntimeError:
    pass
with session.guard("after-boom", timeout=1.0) as got:
    check("异常退出后仍可取（finally 释放）", got is True)

# 4) 开关关闭 → 完全不互斥
os.environ["DZMM_SESSION_LOCK"] = "off"
with session.guard("a", timeout=0.3) as a:
    with session.guard("b", timeout=0.3) as b:
        check("开关关闭时不互斥", a is True and b is True)
os.environ.pop("DZMM_SESSION_LOCK", None)

# 5) 跨进程互斥（真正的生产场景：entrypoint 循环 vs 派生的 headless_relogin）
import subprocess  # noqa: E402

holder = subprocess.Popen(
    [sys.executable, "-c",
     "import sys,time; sys.path.insert(0,'/app');"
     "from runtime import session;"
     "from pathlib import Path;"
     "session.LOCK_FILE=Path(sys.argv[1]); session.VERSION_FILE=Path(sys.argv[2]);"
     "ctx=session.guard('child',timeout=1.0); got=ctx.__enter__();"
     "print('child_got',got,flush=True); time.sleep(3)",
     str(tmp / ".session.lock"), str(tmp / ".session_version.json")],
    stdout=subprocess.PIPE, text=True)
line = holder.stdout.readline().strip()
check("子进程拿到锁", line.endswith("True"), line)
with session.guard("parent", timeout=0.5) as got:
    check("父进程被子进程挡住（跨进程互斥）", got is False)
holder.wait(timeout=10)
with session.guard("parent2", timeout=1.0) as got:
    check("子进程退出后父进程可取", got is True)

# 6) 版本号
session.note_credentials_written("check")
check("版本号递增", session.version() == 1, session.version())

print()
print("FAILED: {}".format(fails) if fails else "ALL CHECKS PASSED")
raise SystemExit(1 if fails else 0)
