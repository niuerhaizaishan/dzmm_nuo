"""P11 第 A 期：按群/Bot 外部配置（`runtime/config_store.py`）的守卫测试。

这一期最重要的一条性质是 **零行为变化**：
`config.json` 不存在时，行为必须与改动前逐字一致。
所以第一组测试盯的就是"默认值路径"和"默认值 ⇄ 文件"的等价性。

另外两条：
  * 坏配置必须**拒绝启动**（不带着半截配置跑）
  * 写路径要**先校验后落盘**、原子替换、留下备份
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import config_store as cs  # noqa: E402

PERSONAS = ("nuo", "rose_butler")
CID_A = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
CID_B = "93cc6c7b-ba9e-48f3-a106-3074483aebbd"
CID_C = "38698306-4252-438a-aa56-7dbab89250eb"
CID_D = "007bfd75-2efb-491c-8233-20046af69b24"
CID_ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"

DEFAULTS = {
    "groups": [CID_A, CID_B, CID_C, CID_D, CID_ROSE],
    "mute": set(),
    "screen": {CID_A, CID_B, CID_C},
    "push": [CID_A, CID_D],
    "aliases": {CID_A: "认证妹子群", CID_ROSE: "Rose（505）玫瑰房"},
    "bots": [{"key": "nuo_main", "name": "小小糯-bot", "short": "小小糯",
              "cid": CID_A, "token_env": "DZMM_BOT_TOKEN", "persona": "nuo"}],
    "personas": PERSONAS,
}


@pytest.fixture
def cfgdir(monkeypatch, dbdir):
    """把配置指向临时目录（默认不存在 → 走 defaults 路径）。"""
    p = dbdir / "config.json"
    monkeypatch.setenv("DZMM_CONFIG", str(p))
    cs._cache = None
    yield p
    cs._cache = None


def _write(p, raw):
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def _gen():
    """用与 bot.py 相同的方式从默认值生成一份配置。"""
    return cs._defaults_to_raw(DEFAULTS)


# ---------------------------------------------------------------- 零行为变化

def test_missing_file_falls_back_to_defaults(cfgdir):
    cfg = cs.load(DEFAULTS)
    assert cfg.source == "defaults"
    assert list(cfg.groups) == DEFAULTS["groups"]
    assert set(cfg.mute) == set(DEFAULTS["mute"])
    assert set(cfg.screen) == DEFAULTS["screen"]
    assert list(cfg.push) == DEFAULTS["push"]


def test_generated_file_is_equivalent_to_defaults(cfgdir):
    """由默认值生成的 config.json，读出来的结果必须与 defaults 完全一致。

    这条是"零行为变化"的核心：`tools/config_gen.py` 生成的首份配置就是这条路径，
    所以首份配置**必然**与现状等价。
    """
    _write(cfgdir, _gen())
    cfg = cs.load(DEFAULTS)
    assert cfg.source == "file"
    assert list(cfg.groups) == DEFAULTS["groups"]
    assert set(cfg.mute) == set(DEFAULTS["mute"])
    assert set(cfg.screen) == DEFAULTS["screen"]
    assert list(cfg.push) == DEFAULTS["push"]
    assert [b.key for b in cfg.bots] == ["nuo_main"]
    assert cfg.bots[0].cid == CID_A
    assert cfg.bots[0].resolve_token({"DZMM_BOT_TOKEN": "tk"}) == "tk"


def test_example_config_parses_and_matches_defaults():
    """仓库里的 config.example.json 必须能过校验，且语义与默认值一致。

    它是给人照抄的模板；模板自己就是坏的，比没有模板更糟。
    """
    raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    cfg = cs._parse(raw, DEFAULTS)
    assert list(cfg.groups) == DEFAULTS["groups"]
    assert set(cfg.screen) == DEFAULTS["screen"]
    assert list(cfg.push) == DEFAULTS["push"]
    assert [b.key for b in cfg.bots] == ["nuo_main"]
    # 模板里必须**不含**明文 token
    assert "token" not in raw["bots"][0]


# ---------------------------------------------------------------- 校验

def test_rejects_duplicate_group(cfgdir):
    raw = _gen()
    raw["groups"].append(dict(raw["groups"][0]))
    with pytest.raises(cs.ConfigError, match="重复"):
        cs._parse(raw, DEFAULTS)


def test_rejects_missing_cid(cfgdir):
    raw = _gen()
    raw["groups"][0]["cid"] = ""
    with pytest.raises(cs.ConfigError, match="缺少 cid"):
        cs._parse(raw, DEFAULTS)


def test_rejects_bot_bound_to_unknown_group(cfgdir):
    """P1「绑定群 ≠ 目标群就拒绝启动」断言的推广：绑定的群必须真在群清单里。

    这条防的是**误发** —— Bot 被配到没打算服务的群上。
    """
    raw = _gen()
    raw["bots"][0]["cid"] = "ffffffff-0000-0000-0000-000000000000"
    with pytest.raises(cs.ConfigError, match="不在 groups 里"):
        cs._parse(raw, DEFAULTS)


def test_rejects_two_bots_on_the_same_group(cfgdir):
    """两个 Bot 绑同一个群 → 无法决定走哪个通道，必须拒绝。"""
    raw = _gen()
    raw["bots"].append(dict(raw["bots"][0], key="nuo_second"))
    with pytest.raises(cs.ConfigError, match="同一个群"):
        cs._parse(raw, DEFAULTS)


def test_rejects_duplicate_bot_key(cfgdir):
    raw = _gen()
    raw["bots"].append(dict(raw["bots"][0], cid=CID_ROSE))
    with pytest.raises(cs.ConfigError, match="key .* 重复"):
        cs._parse(raw, DEFAULTS)


def test_rejects_unknown_persona(cfgdir):
    raw = _gen()
    raw["groups"][0]["persona"] = "不存在的persona"
    with pytest.raises(cs.ConfigError, match="persona"):
        cs._parse(raw, DEFAULTS)


def test_rejects_bot_without_any_token_source(cfgdir):
    raw = _gen()
    raw["bots"][0].pop("token_env", None)
    with pytest.raises(cs.ConfigError, match="token_env"):
        cs._parse(raw, DEFAULTS)


def test_rejects_unsupported_version(cfgdir):
    raw = _gen()
    raw["version"] = 99
    with pytest.raises(cs.ConfigError, match="version"):
        cs._parse(raw, DEFAULTS)


def test_broken_json_refuses_to_start(cfgdir):
    """文件坏了必须**抛异常**（bot.py 会 SystemExit），不能静默退回默认值。

    静默退回 = 你改的东西没生效、而且没有任何提示 —— 比启动失败难查得多。
    """
    cfgdir.write_text("{ 这不是 JSON", encoding="utf-8")
    with pytest.raises(cs.ConfigError, match="解析失败"):
        cs.load(DEFAULTS)


def test_load_is_cached_until_reload(cfgdir):
    cfg1 = cs.load(DEFAULTS)
    _write(cfgdir, _gen())
    assert cs.load(DEFAULTS) is cfg1                 # 仍读缓存
    cfg2 = cs.reload(DEFAULTS)
    assert cfg2.source == "file" and cfg2 is not cfg1


# ---------------------------------------------------------------- 语义

def test_disabled_group_is_dropped_from_groups(cfgdir):
    raw = _gen()
    raw["groups"][1]["enabled"] = False
    cfg = cs._parse(raw, DEFAULTS)
    assert CID_B not in cfg.groups
    assert CID_B in cfg.group_specs           # 规格还在，只是不参与


def test_mute_group_stays_in_groups(cfgdir):
    """静默群**仍在** GROUPS 里 —— 与改动前语义一致（主循环会读它，sync 才跳过）。"""
    raw = _gen()
    raw["groups"][1]["mute"] = True
    cfg = cs._parse(raw, DEFAULTS)
    assert CID_B in cfg.groups
    assert CID_B in cfg.mute


def test_push_only_contains_active_groups(cfgdir):
    raw = _gen()
    raw["groups"][0]["push"] = True
    raw["groups"][0]["enabled"] = False
    cfg = cs._parse(raw, DEFAULTS)
    assert CID_A not in cfg.push


def test_group_and_bot_lookup(cfgdir):
    cfg = cs._parse(_gen(), DEFAULTS)
    assert cfg.bot_for(CID_A).key == "nuo_main"
    assert cfg.bot_for(CID_ROSE) is None          # 玫瑰群还没 Bot
    assert cfg.group(CID_ROSE).alias.startswith("Rose")
    assert cfg.group("nope") is None


# ---------------------------------------------------------------- 写路径

def test_save_validates_before_writing(cfgdir):
    """校验不过时**一个字都不写**，已有文件保持不变。"""
    _write(cfgdir, _gen())
    before = cfgdir.read_text(encoding="utf-8")
    bad = _gen()
    bad["bots"][0]["cid"] = "ffffffff-0000-0000-0000-000000000000"
    with pytest.raises(cs.ConfigError):
        cs.save(bad, DEFAULTS)
    assert cfgdir.read_text(encoding="utf-8") == before
    assert not cs.backup_dir().exists()


def test_save_is_atomic_and_leaves_no_tmp(cfgdir):
    cs.save(_gen(), DEFAULTS)
    assert cfgdir.is_file()
    assert not cfgdir.with_suffix(".json.tmp").exists()


def test_save_backs_up_previous_version(cfgdir):
    cs.save(_gen(), DEFAULTS)
    raw = _gen()
    raw["groups"][1]["mute"] = True
    cs.save(raw, DEFAULTS)
    backups = sorted(cs.backup_dir().glob("config-*.json"))
    assert len(backups) == 1, backups
    old = json.loads(backups[0].read_text(encoding="utf-8"))
    assert old["groups"][1].get("mute", False) is False   # 备份里是第一版


def test_save_keeps_bounded_number_of_backups(cfgdir, monkeypatch):
    monkeypatch.setattr(cs, "KEEP_BACKUPS", 3)
    import time as _t
    for i in range(6):
        raw = _gen()
        raw["groups"][1]["alias"] = "v{}".format(i)
        cs.save(raw, DEFAULTS)
        _t.sleep(1.01)                 # 备份名精确到秒
    assert len(list(cs.backup_dir().glob("config-*.json"))) <= 3


def test_saved_file_is_human_readable_json(cfgdir):
    """必须是**人能直接看/直接改**的 JSON —— 界面挂了要能 SSH 改回来救。"""
    cs.save(_gen(), DEFAULTS)
    text = cfgdir.read_text(encoding="utf-8")
    assert "\n" in text and text.endswith("\n")
    assert json.loads(text)["version"] == cs.SCHEMA_VERSION


def test_as_dict_roundtrips(cfgdir):
    cfg = cs._parse(_gen(), DEFAULTS)
    again = cs._parse(cfg.as_dict(), DEFAULTS)
    assert list(again.groups) == list(cfg.groups)
    assert set(again.screen) == set(cfg.screen)
    assert [b.key for b in again.bots] == [b.key for b in cfg.bots]


def test_defaults_to_raw_does_not_alias_caller_dicts():
    """`_defaults_to_raw` 必须深拷贝。

    否则调用方改返回值的 `bots[i]`，就把**模块级默认值**改掉了。
    这正是本文件第一版踩的坑：某个用例改了自己的 `_gen()` 结果，
    结果后面十几个用例全红 —— 而"原因是别名"这件事从报错里完全看不出来。
    """
    defaults = dict(DEFAULTS)
    raw = cs._defaults_to_raw(defaults)
    raw["bots"][0]["cid"] = "ffffffff-0000-0000-0000-000000000000"
    raw["bots"][0]["key"] = "tampered"
    assert defaults["bots"][0]["cid"] == CID_A
    assert defaults["bots"][0]["key"] == "nuo_main"


def test_generated_config_does_not_alias_repo_defaults():
    """生产同款风险：bot.py 传进来的 `_BOTS_DEFAULT` 不能被生成结果带偏。"""
    bots = [{"key": "k", "name": "n", "cid": CID_A, "token_env": "T"}]
    defaults = dict(DEFAULTS, bots=bots)
    raw = cs._defaults_to_raw(defaults)
    raw["bots"][0]["name"] = "改过了"
    assert bots[0]["name"] == "n"
