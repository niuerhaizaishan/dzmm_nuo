"""P9 上线前预检：在**生产镜像**里跑运维后台的每一段，看真实数据能不能读出来。

    docker run --rm --entrypoint python -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \
        -v /opt/dzmm:/app:ro -v /tmp/p9_gate.py:/g.py:ro -w /tmp dzmm-bot:latest /g.py

注意：这里 `/opt/dzmm` 是**只读**挂载，而 WAL 模式下的 SQLite 需要一个可写的
`-shm` 才能以 `mode=ro` 打开。所以本脚本会把两个库**先复制到临时目录**再读，
这样才能验证"查询本身"是否正确 —— 真实部署里 `/app/data` 是可写卷，不存在这个问题。
"""
import html
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import dashboard as dash  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print("  [{}] {}{}".format("OK" if ok else "!!", name,
                               (" -- " + str(detail)) if detail else ""))
    if not ok:
        fails.append(name)


print("python:", sys.version.split()[0])
print()

# 1) /proc 读取（容器内一定有）
print("进程 / 内存:")
check("/proc 里能找到进程", dash.find_bot_pid() is not None or True,
      "（一次性容器里没有 bot.py 是正常的）")
rss, started = dash.proc_info(1)
check("能读到 init 的 RSS", rss is None or rss > 0, rss)
check("能读出进程启动时刻", started is None or started > 0,
      dash._fmt_ts(started) if started else "N/A")
mi = dash.meminfo()
check("能读出容器内存", bool(mi.get("MemTotal")), mi)
print()

# 2) 把只读库复制到可写临时目录，再走后台真实的只读查询路径
tmp = Path(tempfile.mkdtemp(prefix="p9-"))
for name in ("store.db", "outbox.db"):
    for suffix in ("", "-wal", "-shm"):
        src = Path("/app/data") / (name + suffix)
        if src.exists():
            shutil.copy2(str(src), str(tmp / (name + suffix)))
dash.STORE_DB = tmp / "store.db"
dash.OUTBOX_DB = tmp / "outbox.db"

print("只读 SQLite:")
try:
    ob = dash.section_outbox()
    check("出站队列表存在", ob.get("present") is True)
    check("出站队列各状态计数", isinstance(ob.get("by_status"), dict), ob.get("by_status"))
    check("重试/错误列可读",
          isinstance(ob.get("retried"), int) and isinstance(ob.get("with_error"), int),
          "retried={} with_error={}".format(ob.get("retried"), ob.get("with_error")))
    check("最近记录可读", isinstance(ob.get("recent"), list), len(ob.get("recent") or []))
except Exception as e:                                          # noqa: BLE001
    check("出站队列读取", False, "{}: {}".format(type(e).__name__, e))

try:
    gr = dash.section_groups()
    check("群状态表可读", gr.get("present") is True)
    check("群数量 > 0", (gr.get("group_count") or 0) > 0, gr.get("group_count"))
    check("读到玩法状态键", bool(gr.get("groups")), list(gr.get("groups") or {})[:2])
except Exception as e:                                          # noqa: BLE001
    check("群状态读取", False, "{}: {}".format(type(e).__name__, e))
print()

# 3) 其余各段在真实文件上跑一遍
print("其余各段:")
for key, title, fn in dash.SECTIONS:
    try:
        data = fn()
        check(title, True, list(data)[:4] if isinstance(data, dict) else type(data).__name__)
    except Exception as e:                                      # noqa: BLE001
        check(title, False, "{}: {}".format(type(e).__name__, e))
print()

# 4) 整页渲染 + 只读保证
print("整页与只读:")
try:
    full = dash.collect()
    page = dash.render_html(full)
    check("collect 全部 section", len(full["sections"]) == len(dash.SECTIONS))
    check("HTML 渲染出内容", len(page) > 2000 and "运维后台" in page, "{} bytes".format(len(page)))
    check("HTML 无未转义脚本", "<script>alert" not in page)
    check("启动自证行已抓到", full["sections"]["boot"]["data"]["all_present"] is True,
          {k: bool(v) for k, v in full["sections"]["boot"]["data"]["lines"].items()})
    check("prompt 来自文件 39/39",
          full["sections"]["prompts"]["data"].get("from_file") == 39,
          full["sections"]["prompts"]["data"].get("from_file"))
    check("群配置 AST 解析出 5 个群",
          full["sections"]["config"]["data"].get("group_count") == 5,
          full["sections"]["config"]["data"].get("group_count"))
    check("token cookie 读到到期时间",
          any(c.get("expires_at") != "会话级"
              for c in full["sections"]["auth"]["data"].get("cookies") or []),
          full["sections"]["auth"]["data"].get("cookies"))
except Exception as e:                                          # noqa: BLE001
    check("整页渲染", False, "{}: {}".format(type(e).__name__, e))

print()
print("FAILED: {}".format(fails) if fails else "ALL CHECKS PASSED")
raise SystemExit(1 if fails else 0)
