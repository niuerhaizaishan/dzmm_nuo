"""人设文本/话术的覆盖层（P12-E · `personas.py` 那一半）。

到 P12-E 之前，"改人设"必须改代码：文本写死在 `PERSONAS`、话术写死在 `PHRASES`。
现在配置能覆盖它们，本文件钉住四件事：

1. 覆盖**真的生效**（名字替换、extra 追加、`p()` 换话术），
   且只影响被覆盖的那个人设；
2. 覆盖是"覆盖"不是"替换"：没写的一律还是代码默认（零行为变化）；
3. **空串是一种值**（`extra: ""` = 这个人设不要专属设定），
   不能当"没写"丢掉 —— 那会让"清空"静默退回默认的长文本；
4. 临时覆盖（`use_overrides`，页面"试说一句"用）走 contextvars：
   出了 with 块就没了，**不碰全局**（面板是多线程的）。
"""
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import personas as P  # noqa: E402

ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
NUO_GROUP = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


@pytest.fixture(autouse=True)
def _clean():
    P.set_content_overrides({})
    P.set_config_personas({})
    yield
    P.set_content_overrides({})
    P.set_config_personas({})
    P.set_cid("")


# ---------------------------------------------------------------- 1. 生效

def test_no_override_is_the_old_behaviour():
    """没有覆盖时，一切与改动前逐字相同（回归钉）。"""
    for cid in (ROSE, NUO_GROUP):
        key = P.persona_key_for(cid)
        assert P.persona_for(cid) == P.PERSONAS[key]
        dph = dict(P.PHRASES["nuo"])
        dph.update(P.PHRASES.get(key) or {})
        assert P.phrases_for(key) == dph
    base = "你是" + P.DEFAULT_NAME_FULL + "（简称" + P.DEFAULT_NAME_SHORT + "）。"
    assert P.apply_persona(base, NUO_GROUP) == base, "默认人设要零开销原样返回"


def test_name_and_extra_override_take_effect():
    P.set_content_overrides({"rose_butler": {
        "name_full": "玫瑰管家～", "name_short": "玫瑰管家",
        "extra": "【覆盖】只听 Vv姐姐 的。"}})
    p = P.persona_for(ROSE)
    assert p["name_full"] == "玫瑰管家～" and p["name_short"] == "玫瑰管家"
    sp = P.apply_persona("你是{0}，简称{1}。".format(P.DEFAULT_NAME_FULL,
                                                 P.DEFAULT_NAME_SHORT), ROSE)
    assert "玫瑰管家～" in sp and "玫瑰管家" in sp
    assert sp.endswith("【覆盖】只听 Vv姐姐 的。")
    assert P.DEFAULT_NAME_FULL not in sp, "全名也要被替换掉"


def test_override_only_affects_that_persona():
    before = dict(P.persona_for(NUO_GROUP))
    P.set_content_overrides({"rose_butler": {"name_short": "只改这个"}})
    assert P.persona_for(NUO_GROUP) == before, "别的人设一个字都不能变"


def test_empty_extra_means_no_extra_not_default():
    """空串 = "这个人设不要专属设定"（不是"没写"）。

    这是踩过的坑：早先 `_normalize` 把空串当"没写"丢掉，
    于是"清空 extra"的保存会**静默退回**代码里那段一千多字的默认设定。
    """
    assert P.PERSONAS["rose_butler"]["extra"], "前置条件：房间里本来有一段 extra"
    P.set_content_overrides({"rose_butler": {"extra": ""}})
    assert P.persona_for(ROSE)["extra"] == ""
    sp = P.apply_persona("你是" + P.DEFAULT_NAME_FULL + "。", ROSE)
    assert sp == "你是" + P.DEFAULT_NAME_FULL + "。", "空的 extra 不该追加任何东西"


def test_phrase_override_and_empty_phrase():
    P.set_content_overrides({"rose_butler": {"phrases": {
        "mem_saved": "记下了，{name}。", "no_vote": ""}}})
    assert P.p("mem_saved", ROSE, name="客人") == "记下了，客人。"
    assert P.p("no_vote", ROSE) == "", "空话术就是空话术，不能回落到默认"
    # 没被覆盖的话术还是本群原来的那份
    assert P.p("not_found", ROSE) == P.PHRASES["rose_butler"]["not_found"]
    # 别的人设不受影响
    assert P.p("mem_saved", NUO_GROUP) == P.PHRASES["nuo"]["mem_saved"]


def test_set_content_overrides_ignores_junk():
    P.set_content_overrides({"nuo": "不是对象", "": {}, "rose_butler": None})
    assert P.content_overrides() == {}


# ---------------------------------------------------------------- 2. 临时覆盖

def test_use_overrides_is_scoped_and_restores():
    P.set_content_overrides({"nuo": {"name_short": "全局的"}})
    with P.use_overrides({"nuo": {"name_short": "临时的"}}):
        assert P.persona_for(NUO_GROUP)["name_short"] == "临时的"
    assert P.persona_for(NUO_GROUP)["name_short"] == "全局的", "出了 with 块必须还原"


def test_use_overrides_does_not_touch_globals():
    """关键：临时覆盖**不能**改模块全局（面板是多线程的，改全局再改回来会串台）。"""
    seen = []

    def worker():
        seen.append(P.persona_for(NUO_GROUP)["name_short"])

    with P.use_overrides({"nuo": {"name_short": "只在我这里"}}):
        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert P.persona_for(NUO_GROUP)["name_short"] == "只在我这里"
    assert P.content_overrides() == {}, "全局覆盖表一个字都没被改"
    assert seen == [P.PERSONAS["nuo"]["name_short"]], "别的线程看不到临时覆盖"


def test_use_overrides_nested_and_exception_safe():
    with P.use_overrides({"nuo": {"name_short": "外层"}}):
        with P.use_overrides({"nuo": {"name_short": "内层"}}):
            assert P.persona_for(NUO_GROUP)["name_short"] == "内层"
        assert P.persona_for(NUO_GROUP)["name_short"] == "外层"
    try:
        with P.use_overrides({"nuo": {"name_short": "会炸"}}):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert P.persona_for(NUO_GROUP)["name_short"] == P.PERSONAS["nuo"]["name_short"]


# ---------------------------------------------------------------- 3. 登记册

def test_phrase_labels_cover_every_key():
    """新增一个话术 key 必须同时补中文标签 —— 否则页面上只有一串英文。"""
    for key in P.PERSONAS:
        assert set(P.known_phrase_keys(key)) <= set(P.PHRASE_LABELS)


def test_known_phrase_keys_is_code_based():
    """登记册只认代码里的话术：覆盖里塞的野 key 不能把自己变成"合法"。"""
    P.set_content_overrides({"nuo": {"phrases": {"野的key": "x"}}})
    assert "野的key" not in P.known_phrase_keys("nuo")
    assert P.phrases_for("nuo")["野的key"] == "x"   # 但确实会生效（校验在配置层）


# ---------------------------------------------------------------- 4. 体检

def test_health_flags_bad_values():
    long_txt = "啊" * (P.MSG_MAX_CHARS + 1)
    P.set_content_overrides({"nuo": {
        "name_short": "带\n换行",
        "phrases": {"mem_saved": long_txt}}})
    issues = P.health("nuo")
    assert any("mem_saved" in x and "上限" in x for x in issues)
    assert any("换行" in x for x in issues)


def test_health_checks_placeholders():
    P.set_content_overrides({"nuo": {"phrases": {"mem_saved": "记住了{nam}。"}}})
    issues = P.health("nuo")
    assert any("占位符" in x for x in issues)


def test_health_flags_emptied_phrase_but_not_originally_empty():
    """把本来有内容的话术清空 = 提示；本来就是空的（如 nuo 的 help_text）不算问题。"""
    P.set_content_overrides({"nuo": {"phrases": {"no_vote": ""}}})
    assert any("no_vote" in x for x in P.health("nuo"))
    assert not any("help_text" in x for x in P.health("nuo"))


def test_health_unknown_persona():
    assert P.health("no_such_persona")


def test_summary_and_describe():
    P.set_content_overrides({"rose_butler": {
        "name_short": "玫瑰管家", "phrases": {"mem_saved": "记下了"}}})
    s = P.summary()
    assert s["personas"] == 1 and s["fields"] == 1 and s["phrases"] == 1
    d = P.describe_persona("rose_butler")
    assert d["overrides"]["name_short"] == "玫瑰管家"
    assert d["effective"]["name_short"] == "玫瑰管家"
    assert d["default"]["name_short"] == P.PERSONAS["rose_butler"]["name_short"]
    assert d["untouched"] is False
