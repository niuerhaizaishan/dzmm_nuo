"""schema v2（P12-C）：迁移 / 新三块校验 / 端到端进运行期。

这一步的验收标准有三条：

1. **旧文件必须能读**：v1 配置在内存里升级成 v2（行为不变），
   启动时还能**落盘升级 + 留备份**（`migrate_file`）。
2. **新三块校验得住**：功能开关/参数/按群集合里写错名字、越界、类型不对，
   一律 `ConfigError`（**不许静默忽略** —— 那会变成"页面上配了、其实没生效"）。
3. **配了就得真生效**：起一个子进程 `import bot`，让它吃一份 v2 配置，
   验证功能开关/参数/集合都真的到了运行期。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import config_store as cs  # noqa: E402

CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "4a56be71-9ca6-4785-b45f-18725d187f1a"

BASE = {
    "groups": [{"cid": CID_A, "alias": "A"}, {"cid": CID_B, "alias": "B"}],
    "mute": (), "screen": (), "push": (),
    "aliases": {},
    "bots": [{"key": "k", "name": "n", "cid": CID_A, "token_env": "T"}],
    "personas": ("nuo", "rose_butler"),
}

V1 = {
    "version": 1,
    "groups": [{"cid": CID_A, "alias": "认证群", "enabled": True,
                "mute": False, "screen": True, "push": True, "persona": ""},
               {"cid": CID_B, "alias": "玫瑰房", "enabled": True,
                "mute": False, "screen": False, "push": False, "persona": "rose_butler"}],
    "bots": [{"key": "nuo_main", "name": "小小糯-bot", "short": "小小糯",
              "cid": CID_A, "token_env": "DZMM_BOT_TOKEN", "persona": "nuo"}],
}


@pytest.fixture
def cfgfile(monkeypatch, dbdir):
    p = dbdir / "config.json"
    monkeypatch.setenv("DZMM_CONFIG", str(p))
    cs._cache = None
    yield p
    cs._cache = None


# ---------------------------------------------------------------- 1. 迁移

def test_v1_loads_and_is_upgraded_in_memory(cfgfile):
    cfgfile.write_text(json.dumps(V1, ensure_ascii=False), encoding="utf-8")
    cfg = cs.load(BASE)
    assert cfg.migrated_from == 1, "读到 v1 要记下'从哪版升上来的'"
    assert cfg.source == "file"
    # 行为不变：群、开关、Bot 全都跟 v1 写的一样
    assert cfg.groups == (CID_A, CID_B)
    assert cfg.screen == frozenset({CID_A})
    assert cfg.push == (CID_A,)
    assert cfg.bots[0].key == "nuo_main"
    assert cfg.features == {} and cfg.tuning == {} and cfg.scopes == {}, \
        "升级只是补上空节，不能凭空造出覆盖"


def test_v1_file_is_migrated_on_disk_with_backup(cfgfile):
    cfgfile.write_text(json.dumps(V1, ensure_ascii=False), encoding="utf-8")
    report = cs.migrate_file(BASE)
    assert report["migrated"] is True
    assert report["from"] == 1 and report["to"] == cs.SCHEMA_VERSION
    assert report["backup"], "升级前必须留备份"
    assert (cs.backup_dir() / report["backup"]).is_file()
    on_disk = json.loads(cfgfile.read_text(encoding="utf-8"))
    assert on_disk["version"] == cs.SCHEMA_VERSION
    # 备份里仍然是原来那一版（可回滚）
    old = json.loads((cs.backup_dir() / report["backup"]).read_text(encoding="utf-8"))
    assert old["version"] == 1


def test_migrate_file_is_idempotent(cfgfile):
    cfgfile.write_text(json.dumps(V1, ensure_ascii=False), encoding="utf-8")
    assert cs.migrate_file(BASE)["migrated"] is True
    again = cs.migrate_file(BASE)
    assert again["migrated"] is False, "已经是当前版本就不该再动文件"
    assert again["error"] == ""


def test_migrate_file_without_file_is_noop(cfgfile):
    r = cs.migrate_file(BASE)
    assert r["migrated"] is False and not r["error"]


def test_unknown_future_version_is_rejected(cfgfile):
    cfgfile.write_text(json.dumps({"version": 99, "groups": [], "bots": []}),
                       encoding="utf-8")
    with pytest.raises(cs.ConfigError) as e:
        cs.load(BASE)
    assert "不受支持" in str(e.value)


def test_v1_round_trip_preserves_behaviour(cfgfile):
    """升级 → 保存 → 再读：运行期语义（群/开关/Bot）必须一字不差。"""
    cfgfile.write_text(json.dumps(V1, ensure_ascii=False), encoding="utf-8")
    cfg = cs.load(BASE)
    before = (cfg.groups, cfg.mute, cfg.screen, cfg.push, cfg.bots[0].key)
    cs.save(cfg.as_dict(), BASE)
    cs._cache = None
    after = cs.load(BASE)
    assert (after.groups, after.mute, after.screen, after.push,
            after.bots[0].key) == before
    assert after.as_dict()["version"] == cs.SCHEMA_VERSION


# ---------------------------------------------------------------- 2. 校验

def _raw(**kw):
    out = {"version": cs.SCHEMA_VERSION,
           "groups": [{"cid": CID_A, "alias": "A"}],
           "bots": [{"key": "k", "cid": CID_A, "token_env": "T"}]}
    out.update(kw)
    return out


def test_feature_override_must_be_registered():
    with pytest.raises(cs.ConfigError) as e:
        cs._parse(_raw(features={"no_such_feature": False}), BASE)
    assert "不是已登记的功能" in str(e.value)


def test_feature_override_accepts_on_object_form():
    cfg = cs._parse(_raw(features={"diaojiao": {"on": False}}), BASE)
    assert cfg.features == {"diaojiao": False}


def test_tuning_override_must_be_registered_and_in_range():
    with pytest.raises(cs.ConfigError):
        cs._parse(_raw(tuning={"no.such.param": 1}), BASE)
    with pytest.raises(cs.ConfigError) as e:
        cs._parse(_raw(tuning={"devil.base_prob": 3.0}), BASE)
    assert "上界" in str(e.value)
    cfg = cs._parse(_raw(tuning={"devil.base_prob": 0.2}), BASE)
    assert cfg.tuning == {"devil.base_prob": pytest.approx(0.2)}


def test_per_group_overrides_are_validated_too():
    raw = _raw(groups=[{"cid": CID_A, "features": {"nope": True}}])
    with pytest.raises(cs.ConfigError):
        cs._parse(raw, BASE)
    raw = _raw(groups=[{"cid": CID_A, "tuning": {"heat.climax_threshold": 0}}])
    with pytest.raises(cs.ConfigError):
        cs._parse(raw, BASE)


def test_scope_name_and_cids_are_validated():
    with pytest.raises(cs.ConfigError) as e:
        cs._parse(_raw(scopes={"trendd": [CID_A]}), BASE)
    assert "不是已登记的按群集合" in str(e.value)
    with pytest.raises(cs.ConfigError) as e:
        cs._parse(_raw(scopes={"trend": ["not-a-real-cid"]}), BASE)
    assert "不在 groups 里" in str(e.value)
    cfg = cs._parse(_raw(scopes={"trend": [CID_A, CID_A]}), BASE)
    assert cfg.scopes == {"trend": (CID_A,)}, "重复的 cid 要去掉"


def test_booleans_must_be_real_booleans():
    """`bool("false")` 是 True —— 字符串 "false" 会被静默当成打开，必须拒。"""
    for field in ("enabled", "mute", "screen", "push"):
        with pytest.raises(cs.ConfigError) as e:
            cs._parse(_raw(groups=[{"cid": CID_A, field: "false"}]), BASE)
        assert field in str(e.value)
    with pytest.raises(cs.ConfigError):
        cs._parse(_raw(features={"diaojiao": "false"}), BASE)


def test_override_maps_mix_global_and_per_group():
    raw = _raw(features={"insult": False},
               tuning={"devil.base_prob": 0.2},
               groups=[{"cid": CID_A, "features": {"vote": False},
                        "tuning": {"dice.timeout": 60}}])
    cfg = cs._parse(raw, BASE)
    fm, tm = cfg.feature_map(), cfg.tuning_map()
    # ⚠️ 2026-10-02：除了 `features` 子表，**群级字段 `screen` 也要桥进这张表**
    # （原先没桥 ⇒ `_feat_on(cid,"screen")` 永远回落到默认 True ⇒ 对账闸门是死的
    #  ⇒ screen=False 的上班群被按认证群规矩踢人）。所以这里多一个键。
    assert fm == {"insult": False,
                  "{}|vote".format(CID_A): False,
                  "{}|screen".format(CID_A): False}, "群级 screen 必须也进覆盖表"
    assert tm["devil.base_prob"] == pytest.approx(0.2)
    assert tm["{}|dice.timeout".format(CID_A)] == 60


def test_empty_sections_are_not_written_back():
    """没有覆盖时不写空节 —— 少点噪声，且"没有这节"="这节是空的"。"""
    cfg = cs._parse(_raw(), BASE)
    d = cfg.as_dict()
    assert "features" not in d and "tuning" not in d and "scopes" not in d


# ---------------------------------------------------------------- 3. 真进运行期

def test_saving_the_page_does_not_wipe_v2_sections(cfgfile):
    """**关键防丢失**：这一版页面只画群和 Bot；保存时必须继承 v2 三节。

    不继承的话，一次"改个群别名"的保存就会把用户配好的功能开关、参数、
    按群集合**整节抹掉** —— 静默丢失比报错难查得多。
    """
    from admin import config_ui as ui
    base = {"version": cs.SCHEMA_VERSION,
            "groups": [{"cid": CID_A, "alias": "A", "enabled": True}],
            "bots": [{"key": "k", "cid": CID_A, "token_env": "T"}],
            "features": {"insult": False},
            "tuning": {"devil.base_prob": 0.2},
            "scopes": {"trend": [CID_A]}}
    form = {"csrf": "x",
            "groups[0][cid]": [CID_A], "groups[0][alias]": ["改过的名字"],
            "groups[0][enabled]": ["1"],
            "bots[0][key]": ["k"], "bots[0][cid]": [CID_A],
            "bots[0][token_env]": ["T"], "bots[0][enabled]": ["1"],
            "__groups_seen": CID_A, "__bots_seen": "k"}
    out = ui.parse_form(form, base=base)
    assert out["version"] == cs.SCHEMA_VERSION
    assert out["groups"][0]["alias"] == "改过的名字"
    assert out["features"] == {"insult": False}
    assert out["tuning"] == {"devil.base_prob": 0.2}
    assert out["scopes"] == {"trend": [CID_A]}
    # 端到端：过一遍真校验 + 落盘 + 回读，三节还在
    cs.save(out, BASE)
    cs._cache = None
    back = cs.load(BASE)
    assert back.features == {"insult": False}
    assert back.tuning == {"devil.base_prob": pytest.approx(0.2)}
    assert back.scopes == {"trend": (CID_A,)}


def test_v2_config_reaches_runtime_end_to_end(dbdir):
    """起子进程 `import bot`，让它吃一份 v2 配置：三块覆盖必须真的生效。"""
    import os
    import subprocess

    p = dbdir / "config.json"
    p.write_text(json.dumps({
        "version": cs.SCHEMA_VERSION,
        "groups": [{"cid": CID_A, "alias": "认证群", "enabled": True,
                    "screen": True, "push": True,
                    "features": {"vote": False},
                    "tuning": {"dice.timeout": 42}}],
        "bots": [{"key": "k", "name": "n", "cid": CID_A, "token_env": "DZMM_BOT_TOKEN"}],
        "features": {"insult": False},
        "tuning": {"devil.base_prob": 0.25},
        "scopes": {"trend": [CID_A], "recap": []},
    }, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    env.update({"DZMM_CONFIG": str(p), "DZMM_BOT_TOKEN": "dummy"})
    code = (
        "import bot, json;"
        "from runtime import features as F, tuning as T;"
        "print(json.dumps({"
        "'migrated': bot._CFG.migrated_from,"
        "'insult_global': F.on('x', 'insult'),"
        "'vote_this_group': F.on(%r, 'vote'),"
        "'vote_other': F.on('other', 'vote'),"
        "'dice_timeout_this': T.get('dice.timeout', %r),"
        "'dice_timeout_other': T.get('dice.timeout', 'other'),"
        "'devil': T.get('devil.base_prob'),"
        "'trend_here': T.in_scope('trend', %r),"
        "'recap_none': T.in_scope('recap', %r),"
        "}))" % (CID_A, CID_A, CID_A, CID_A)
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, (out.stdout or "")[-400:] + (out.stderr or "")[-600:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["migrated"] == 0
    assert got["insult_global"] is False, "全局功能开关要生效"
    assert got["vote_this_group"] is False, "按群功能开关要生效"
    assert got["vote_other"] is True, "别的群不受影响"
    assert got["dice_timeout_this"] == 42, "按群参数要生效"
    assert got["dice_timeout_other"] == 180, "别的群用默认值"
    assert got["devil"] == pytest.approx(0.25), "全局参数要生效"
    assert got["trend_here"] is True and got["recap_none"] is False, "按群集合要生效"
