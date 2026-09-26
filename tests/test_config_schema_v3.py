"""schema v3（P12-E）：`personas` 一节 —— 人设文本/话术的覆盖。

验收标准三条：

1. **旧文件必须能读**：v1/v2 在内存里升级成 v3（行为不变），
   启动时还能落盘升级 + 留备份。
2. **校验得住**：不认识的人设/字段/话术 key、空名字、超长话术、
   类型不对，一律 `ConfigError` —— **不许静默忽略**（"我改了没生效"
   是最难查的一类问题）。
3. **空串是有意义的值**：`extra: ""` 落盘后读回来还是 ""，
   不能被当成"没写"（那会让"清空 extra"静默退回代码默认的长文本）。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import personas as P  # noqa: E402
from runtime import config_store as cs  # noqa: E402

CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "4a56be71-9ca6-4785-b45f-18725d187f1a"

BASE = {
    "groups": [{"cid": CID_A, "alias": "A"}, {"cid": CID_B, "alias": "B"}],
    "mute": (), "screen": (), "push": (),
    "aliases": {},
    "bots": [{"key": "k", "name": "n", "cid": CID_A, "token_env": "T"}],
    "personas": tuple(P.PERSONAS),
}

V2 = {
    "version": 2,
    "groups": [{"cid": CID_A, "alias": "认证群", "enabled": True,
                "screen": True, "push": True, "persona": ""}],
    "bots": [{"key": "nuo_main", "name": "小小糯-bot", "cid": CID_A,
              "token_env": "DZMM_BOT_TOKEN"}],
    "features": {"insult": False},
    "tuning": {"heat.climax_threshold": 120},
}


def _v3(personas_section):
    raw = json.loads(json.dumps(V2, ensure_ascii=False))
    raw["version"] = 3
    raw["personas"] = personas_section
    return raw


@pytest.fixture
def cfgfile(monkeypatch, dbdir):
    p = dbdir / "config.json"
    monkeypatch.setenv("DZMM_CONFIG", str(p))
    cs._cache = None
    yield p
    cs._cache = None


# ---------------------------------------------------------------- 1. 版本与迁移

def test_schema_version_is_3_and_old_versions_supported():
    assert cs.SCHEMA_VERSION == 3
    assert set(cs.SUPPORTED_VERSIONS) >= {1, 2, 3}


def test_v2_still_loads_and_is_upgraded_in_memory(cfgfile):
    cfgfile.write_text(json.dumps(V2, ensure_ascii=False), encoding="utf-8")
    cfg = cs.load(BASE)
    assert cfg.migrated_from == 2
    assert cfg.personas == {}, "升级不能凭空造出人设覆盖"
    # v2 的三块覆盖照旧
    assert cfg.features == {"insult": False}
    assert cfg.tuning == {"heat.climax_threshold": 120}


def test_v2_file_is_migrated_on_disk_with_backup(cfgfile):
    cfgfile.write_text(json.dumps(V2, ensure_ascii=False), encoding="utf-8")
    report = cs.migrate_file(BASE)
    assert report["migrated"] is True and report["from"] == 2
    assert report["backup"] and (cs.backup_dir() / report["backup"]).is_file()
    old = json.loads((cs.backup_dir() / report["backup"]).read_text(encoding="utf-8"))
    assert old["version"] == 2, "备份里必须是原来那一版（可回滚）"
    assert json.loads(cfgfile.read_text(encoding="utf-8"))["version"] == 3


# ---------------------------------------------------------------- 2. 读写往返

def test_personas_roundtrip_via_save(cfgfile):
    raw = _v3({"rose_butler": {"name_short": "玫瑰管家", "extra": "",
                               "phrases": {"mem_saved": "记下了，{name}。"}}})
    cfg = cs.save(raw, BASE)
    assert cfg.personas["rose_butler"]["name_short"] == "玫瑰管家"
    assert cfg.personas["rose_butler"]["extra"] == ""
    on_disk = json.loads(cfgfile.read_text(encoding="utf-8"))
    assert on_disk["personas"]["rose_butler"]["extra"] == "", \
        "空串要落盘（它表示'这个人设不要专属设定'）"


def test_as_dict_keeps_empty_extra_and_orders_keys(cfgfile):
    raw = _v3({"rose_butler": {"extra": "", "name_full": "玫瑰管家～",
                               "phrases": {"no_vote": "", "mem_saved": "x"}}})
    cfg = cs._parse(raw, BASE)
    out = cfg.as_dict()["personas"]["rose_butler"]
    assert out["phrases"] == {"mem_saved": "x", "no_vote": ""}, \
        "空话术也要留着（那是'这条话术留空'，不是'没写'）"
    assert out["extra"] == ""
    assert list(out) == ["name_full", "extra", "phrases"], "字段顺序固定，diff 才稳定"


def test_empty_personas_section_is_omitted_when_serialising(cfgfile):
    """空节不出现在 `as_dict()` 里（面板/对账都走它，少点噪声）。

    ⚠️ `save()` 是**照 raw 原样写**的（与 features/tuning 一致），
    所以"文件里出现一个空的 personas: {}"是允许的、也是无害的 ——
    读回来就是"没有覆盖"。但页面自己合成新 raw 时会把它删掉。
    """
    cfg = cs.save(_v3({}), BASE)
    assert cfg.personas == {}
    assert "personas" not in cfg.as_dict()
    assert json.loads(cfgfile.read_text(encoding="utf-8"))["personas"] == {}


def test_persona_texts_shape_for_runtime(cfgfile):
    """`Config.persona_texts()` 就是给 `personas.set_content_overrides()` 的形状。"""
    cfg = cs._parse(_v3({"nuo": {"extra": "额外"}}), BASE)
    got = cfg.persona_texts()
    P.set_content_overrides(got)
    try:
        assert P.persona_for(CID_A)["extra"] == "额外"
    finally:
        P.set_content_overrides({})


# ---------------------------------------------------------------- 3. 校验

@pytest.mark.parametrize("section,needle", [
    ({"typo_persona": {"extra": "x"}}, "不是已登记的人设"),
    ({"nuo": {"name_ful": "x"}}, "不认识的字段"),
    ({"nuo": {"phrases": {"typo_phrase": "x"}}}, "不是已登记的话术"),
    ({"nuo": {"name_short": ""}}, "不能为空"),
    ({"nuo": {"name_full": "带\n换行"}}, "不能有换行"),
    ({"nuo": {"name_short": "名" * 41}}, "太长"),
    ({"nuo": {"extra": 123}}, "必须是字符串"),
    ({"nuo": {"phrases": ["not", "a", "dict"]}}, "必须是对象"),
    ({"nuo": "不是对象"}, "必须是对象"),
])
def test_bad_sections_are_rejected(section, needle):
    with pytest.raises(cs.ConfigError) as e:
        cs._parse(_v3(section), BASE)
    assert needle in str(e.value)


def test_platform_limits_on_phrases():
    """话术是**要发出去的整条消息**：平台长度/换行限制必须卡住。"""
    with pytest.raises(cs.ConfigError) as e:
        cs._parse(_v3({"nuo": {"phrases": {
            "mem_saved": "啊" * (P.MSG_MAX_CHARS + 1)}}}), BASE)
    assert "平台上限" in str(e.value)

    with pytest.raises(cs.ConfigError) as e:
        cs._parse(_v3({"nuo": {"phrases": {
            "mem_saved": "行\n" * (P.MSG_MAX_NEWLINES + 2)}}}), BASE)
    assert "平台上限" in str(e.value)

    # 正好卡在边界上要放行
    ok = cs._parse(_v3({"nuo": {"phrases": {
        "mem_saved": "行" + "\n" * P.MSG_MAX_NEWLINES}}}), BASE)
    assert ok.personas["nuo"]["phrases"]["mem_saved"]


def test_extra_length_cap():
    with pytest.raises(cs.ConfigError) as e:
        cs._parse(_v3({"nuo": {"extra": "啊" * (P.EXTRA_MAX_CHARS + 1)}}), BASE)
    assert "超过上限" in str(e.value)


def test_unknown_persona_in_groups_uses_the_same_whitelist():
    """人设白名单只有一份（`personas.PERSONAS`）：这里也用它。"""
    raw = _v3({})
    raw["groups"][0]["persona"] = "no_such"
    with pytest.raises(cs.ConfigError):
        cs._parse(raw, BASE)
