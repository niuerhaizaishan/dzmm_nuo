"""P8 第 3 步的生产端到端证明：prompt 覆盖**真的**改变取值。

在**生产镜像**里跑，但完全不碰正在运行的 bot：
挂载的 prompt 目录换成一份只存在于本进程临时目录里的覆盖文件。

    docker run --rm --entrypoint python -e PYTHONPATH=/app \
        -v /opt/dzmm:/app:ro -v /tmp/p8_override_probe.py:/p.py:ro -w /tmp \
        dzmm-bot:latest /p.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")

MARKER = "【覆盖探针】这一行只存在于临时覆盖文件里，代码默认值里没有。"
NAME = "CMD_PROMPT"


def show(tag, value):
    print("{} len={} 首行={!r}".format(tag, len(value), value.splitlines()[0][:60]))


# 1) 默认（代码里的字面量，DZMM_PROMPTS=off 强制走默认值）
os.environ["DZMM_PROMPTS"] = "off"
sys.modules.pop("brains", None)
import brains  # noqa: E402

base = getattr(brains, NAME)
show("默认值", base)
assert MARKER not in base, "探针标记不该出现在默认值里"

# 2) 覆盖：临时目录里放一份同名 prompt
sys.modules.pop("brains", None)
ovr = Path(tempfile.mkdtemp(prefix="p8-probe-"))
(ovr / (NAME + ".md")).write_text(MARKER + "\n" + base, encoding="utf-8")
os.environ.pop("DZMM_PROMPTS", None)
os.environ["DZMM_PROMPT_DIR"] = str(ovr)

import runtime.prompts as rp  # noqa: E402
rp._sources.clear()
import brains as brains2  # noqa: E402

got = getattr(brains2, NAME)
show("覆盖后", got)

ok = got.startswith(MARKER) and got.endswith(base) and len(got) == len(base) + len(MARKER) + 1
print()
print("覆盖生效:", ok)
print("来源:", rp.sources().get(NAME))

# 3) 反向：同一次运行里把开关关掉，必须回到默认值
os.environ["DZMM_PROMPTS"] = "off"
back = rp.load(NAME, "DEFAULT-SENTINEL")
print("开关关闭后回到默认值:", back == "DEFAULT-SENTINEL")

raise SystemExit(0 if ok else 1)
