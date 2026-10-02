"""补三个切片缺的 import（一次性）。

perf / fasao / diaojiao 各缺一两个导入，dry-run 的"未解析依赖"直接点名了。
按依赖注入的方式补到 slice header 里，不改动被搬的代码本身。
"""
from pathlib import Path

P = Path(__file__).resolve().parent / "p8s4_move.py"
t = P.read_text(encoding="utf-8")

PATCHES = [
    # perf: 缺 generate_person_summary / get_messages_since / send_weixin
    ("""import asyncio
import random
import time
from datetime import datetime, timedelta

from brains import (generate_perf_comment, generate_perf_ending,
                    generate_perf_enrich, generate_perf_insult,
                    generate_perf_novel, generate_perf_novel_chunked)
from dzmm import get_messages_all, get_user""",
     """import asyncio
import random
import time
from datetime import datetime, timedelta

from brains import (generate_perf_comment, generate_perf_ending,
                    generate_perf_enrich, generate_perf_insult,
                    generate_perf_novel, generate_perf_novel_chunked,
                    generate_person_summary)
from dzmm import get_messages_all, get_messages_since, get_user
from wx_alert import send_weixin"""),

    # fasao: 缺 get_messages_all / get_profile / _gender_label
    ("""from brains import (generate_heat_fasao, generate_scene, generate_scene_continue,
                    generate_scene_end, generate_scene_rewrite)
from runtime import ctx""",
     """from brains import (generate_heat_fasao, generate_scene, generate_scene_continue,
                    generate_scene_end, generate_scene_rewrite)
from dzmm import get_messages_all
from profiles import get_profile
from runtime import ctx"""),
    ("""from runtime.members import _find_in_profiles, _find_user_in_msgs, _random_member_uid
from runtime.memory import _retrieve_memory
from runtime.obs import log
from runtime.text import _is_stop_word, _strip_parenthesis""",
     """from runtime.members import (_find_in_profiles, _find_user_in_msgs,
                             _gender_label, _random_member_uid)
from runtime.memory import _retrieve_memory
from runtime.obs import log
from runtime.text import _is_stop_word, _strip_parenthesis"""),

    # diaojiao: 缺 get_profile
    ("""from brains import generate_diaojiao, generate_diaojiao_end
from runtime import ctx""",
     """from brains import generate_diaojiao, generate_diaojiao_end
from profiles import get_profile
from runtime import ctx"""),
]

for old, new in PATCHES:
    if old not in t:
        print("!! 没匹配上:\n{}".format(old[:80]))
        raise SystemExit(1)
    t = t.replace(old, new, 1)

P.write_text(t, encoding="utf-8")
print("已补 {} 处 import".format(len(PATCHES)))
