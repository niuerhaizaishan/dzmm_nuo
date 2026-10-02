"""P12-BP：玩法"进行中/占位"提示全部走话术表（别再硬编码 + 别再贴菜单）。

屋主 2026-09-30 批准的第 ① 项。起因是 P12-BD 的同一个病根：
`bot.py` 里还硬编码着四句打断提示（公演/剧情/投票/剧情已开），
**绕过话术表** ⇒ 上班群的若瑾会漏出"哦～…啦～"的旧口吻（小小糯那套），
而且每条都贴一长串参与菜单（"想投票就发 /选 1/2/3 或 /1/2/3…"）——
重复贴菜单正是屋主点名的"工具熊每次都要问要不要调酒"。

处理方式与 P12-BD 的 DJ 提示一致：**代码默认值逐字等于原来的硬编码串**
（没配覆盖的群零回归），人设话术里按人设覆盖。
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot                                                        # noqa: E402
import personas as P                                              # noqa: E402

RUOJIN = "f4ec4c21-fb48-4be2-9972-e4128f65923b"
AUTH = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
SRC = (ROOT / "bot.py").read_text(encoding="utf-8")
SRC_A = (ROOT / "runtime" / "at_chat.py").read_text(encoding="utf-8")  # P3-3-4：`_active_play_hint` 住这里


@pytest.fixture(autouse=True)
def _bind_group(monkeypatch):
    """群→人设的映射平时由 config 注入；测试里自己摆上（否则回落默认人设）。"""
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {RUOJIN: "ruojin"})

# 这几句原来硬编码在 bot.py（`_active_play_hint` 与主分派里）
HARD = ("进行中哦～", "已经进行中啦～", "发骚过程中暂时无法响应")


def test_no_hardcoded_busy_text_left():
    """这几句只许作为 `_ph(cid, "key", "默认值")` 的参数出现，不许裸用。

    做法：先把所有 `_ph(cid, "key", "默认值")` 整段**从源码里删掉**，
    再看剩下的代码里还有没有这些字样 —— 有就是"直接写死一句发出去"。
    （P3-3-4 起 bot.py 与 runtime/at_chat.py 都要扫。）
    """
    for name, src in (("bot.py", SRC), ("runtime/at_chat.py", SRC_A)):
        clean = re.sub(r'_ph\(cid,\s*"[a-z_]+",\s*"(?:[^"\\]|\\.)*"\)', "", src)
        bad = ["{}: {}".format(i, ln.strip()[:70])
               for i, ln in enumerate(clean.splitlines(), 1)
               if any(h in ln for h in HARD)]
        assert bad == [], name + " 还有硬编码（绕过话术表）的进行中提示：\n" + "\n".join(bad)


def test_new_keys_are_registered_in_the_at_path():
    """`_active_play_hint` 四条都要走话术表（@ 那条路的打断保护）。

    P3-3-4 起 `_active_play_hint` 住在 runtime/at_chat.py。
    """
    i = SRC_A.index("def _active_play_hint")
    seg = SRC_A[i:i + 1400]
    for key in ("play_busy_perf", "play_busy_fasao", "play_busy_diaojiao",
                "play_busy_story"):
        assert '"{}"'.format(key) in seg, "@ 打断保护少了 {}".format(key)


def test_defaults_are_unchanged_for_other_groups():
    """没写过这些 key 的群（认证群）必须**逐字**拿到原来的文案 —— 零回归。"""
    assert P.p("play_busy_perf", AUTH) == ""
    assert bot._ph(AUTH, "play_busy_perf", "🎭 公演进行中哦～暂无法响应") == \
        "🎭 公演进行中哦～暂无法响应", "没有覆盖时就是那个默认值"


def test_ruojin_versions_are_statement_first_without_menus():
    for key in ("play_busy_perf", "play_busy_story", "play_busy_vote",
                "perf_busy_dj", "perf_already", "story_already", "vote_already",
                "play_busy_fasao", "play_busy_diaojiao"):
        t = P.p(key, RUOJIN)
        assert t, "{} 必须有若瑾版本".format(key)
        for soft in ("哦～", "啦～", "～。", "呀"):
            assert soft not in t, "{} 还带软糯口吻 {!r}：{}".format(key, soft, t)
        # 不许再贴"参与菜单"（只留怎么收尾）
        for menu in ("想参与", "想继续就发", "或 /1/2/3", "等结束后再试"):
            assert menu not in t, "{} 还在贴菜单 {!r}".format(key, menu)
        assert "PHRASE_LABELS" or key in P.PHRASE_LABELS, "要补中文标签"
