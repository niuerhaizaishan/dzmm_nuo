"""按群人设（P12-0）。

`config.json` 里一直有 `groups[].persona`，运维后台也一直把它画在页面上，
但**运行期从来没人读它** —— 人设只认 `personas.ROOMS` 派生的 `CID_PERSONA`。
于是"页面上选人设"是个装饰品（2026-09-25 穷尽盘点时发现）。

修法：`personas` 层增加一个由 `bot.py` 注入的配置映射，优先级
**配置 > ROOMS 硬编码 > 默认**，并且**判定只有一处**（`persona_key_for`）。
本文件钉住这条链路的四个环节：
  1) 注入后 `persona_for` / `p()` / `apply_persona` 都跟着走；
  2) 没配置的群行为不变（回归）；
  3) `BotSpec` 上那份 persona 已删除（它永远不生效），但旧配置里的该键**不报错**；
  4) 人设名的白名单只有一份（`personas.PERSONAS`），面板与 bot 共用。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "admin"))

import personas  # noqa: E402
import dashboard as dash  # noqa: E402
from runtime import config_store as cs  # noqa: E402

CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
ROSE = "4a56be71-9ca6-4785-b45f-18725d187f1a"
OTHER = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _reset_personas():
    """每个用例都从"没有配置注入"的干净状态开始。"""
    personas.set_config_personas({})
    yield
    personas.set_config_personas({})


# ---------------------------------------------------------------- 注入生效

def test_config_persona_overrides_room_hardcode():
    personas.set_config_personas({OTHER: "rose_butler"})
    assert personas.persona_key_for(OTHER) == "rose_butler"
    assert personas.persona_for(OTHER)["extra"] == personas.PERSONAS["rose_butler"]["extra"]


def test_config_persona_wins_over_rooms():
    """配置 > ROOMS 硬编码：房间群本来固定 rose_butler，配置能把它改回默认。"""
    assert personas.persona_key_for(ROSE) == "rose_butler"      # ROOMS 硬编码
    personas.set_config_personas({ROSE: "nuo"})
    assert personas.persona_key_for(ROSE) == "nuo", "配置必须压过 ROOMS"
    assert personas.is_default(ROSE) is True


def test_unconfigured_group_keeps_old_behaviour():
    """没配置的群一律不变（这是"零行为变化"的回归钉）。"""
    personas.set_config_personas({})
    assert personas.persona_key_for(OTHER) == "nuo"
    assert personas.persona_key_for(ROSE) == "rose_butler"
    assert personas.is_default(OTHER) is True
    assert personas.is_default(ROSE) is False


def test_p_uses_configured_persona_phrases():
    """固定话术也要跟着配置的人设走，否则会出现"人设换了、话术没换"的半吊子状态。"""
    default_txt = personas.p(("unknown_cmd"), cid=OTHER)
    personas.set_config_personas({OTHER: "rose_butler"})
    room_txt = personas.p("unknown_cmd", cid=OTHER)
    assert default_txt and room_txt
    assert default_txt != room_txt or personas.PHRASES["rose_butler"].get("unknown_cmd") is None


def test_apply_persona_appends_configured_extra():
    """extra 必须被追加，名字也必须被换成本群人设名（两件事一起验）。

    ⚠️ 两点别踩：① 默认人设的"零开销原样返回"要用**默认群**验；
    ② 玫瑰房的人设自己就叫薇丝，基准串必须在**设定好人设之后**再用
    `personas.me(cid)` 拼，否则基准里还是旧名字。
    """
    personas.set_config_personas({})
    base = "你是" + personas.DEFAULT_NAME_SHORT + "。"
    assert personas.apply_persona(base, cid=OTHER) == base, "默认人设要零开销原样返回"

    personas.set_config_personas({OTHER: "rose_butler"})
    base = "你是" + personas.me(OTHER) + "。"
    got = personas.apply_persona(base, cid=OTHER)
    assert got.startswith(base)
    assert personas.PERSONAS["rose_butler"]["extra"] in got
    # 默认名换成这个群的人设名（改名不能只改两个字段；extra 照旧追加在后面）
    assert personas.apply_persona("你是" + personas.DEFAULT_NAME_SHORT + "。",
                                  cid=OTHER).startswith("你是" + personas.me(OTHER) + "。")


def test_set_config_personas_ignores_empty_values():
    """空字符串 = "这一群没指定"，不能被当成一个人设名。"""
    personas.set_config_personas({OTHER: "", ROSE: None})
    assert personas.config_personas() == {}
    assert personas.persona_key_for(OTHER) == "nuo"
    assert personas.persona_key_for(ROSE) == "rose_butler"


# ---------------------------------------------------------------- 配置层

BASE = {
    "groups": [{"cid": CID, "alias": "A", "enabled": True}],
    "mute": (), "screen": (), "push": (),
    "aliases": {},
    "bots": [{"key": "k", "name": "n", "cid": CID, "token_env": "T"}],
    "personas": ("nuo", "rose_butler"),
}


def test_group_persona_is_validated():
    raw = {"version": 1,
           "groups": [{"cid": CID, "persona": "typo_persona"}],
           "bots": [{"key": "k", "cid": CID, "token_env": "T"}]}
    with pytest.raises(cs.ConfigError) as e:
        cs._parse(raw, BASE)
    assert "不存在" in str(e.value)


def test_old_config_with_bot_persona_still_loads():
    """旧文件里 `bots[].persona` 是历史遗留（从来没生效过）：不能因此起不来。"""
    raw = {"version": 1,
           "groups": [{"cid": CID, "persona": "nuo"}],
           "bots": [{"key": "k", "cid": CID, "token_env": "T",
                     "persona": "rose_butler"}]}
    cfg = cs._parse(raw, BASE)
    assert cfg.groups == (CID,)
    assert not hasattr(cfg.bots[0], "persona"), "Bot 上不该再有人设字段"
    assert "persona" not in cfg.as_dict()["bots"][0], \
        "序列化回去时也要把它丢掉（否则页面上又是一列装饰品）"


def test_bot_spec_has_no_persona_field():
    import dataclasses
    names = {f.name for f in dataclasses.fields(cs.BotSpec)}
    assert "persona" not in names
    assert {"key", "name", "cid", "token_env", "enabled"} <= names


def test_dashboard_uses_personas_module_as_the_whitelist():
    """面板与 bot 必须共用同一份人设名单（唯一真源）。"""
    assert dash._persona_names() == tuple(personas.PERSONAS)
    assert dash._config_defaults()["personas"] == tuple(personas.PERSONAS)


# ------------------------------------------- 端到端：bot.py 真的注入了配置人设

def test_bot_module_injects_configured_persona(dbdir):
    """**真跑一次 `import bot`**：验证 bot.py 确实把配置里的人设注入了 personas 层。

    单元层面 `set_config_personas` 是好的，但"bot.py 有没有调用它、
    调用时有没有传对 cid"是另一回事 —— 用子进程跑真身，端到端才算数。
    （不用 pytest 的 `tmp_path`：本机沙箱下 tmpdir 插件会 PermissionError，
      所以用本仓库的 `dbdir` 夹具。）
    """
    import json as _json
    import os
    import subprocess

    root = Path(__file__).resolve().parent.parent
    cfg = dbdir / "config.json"
    cfg.write_text(_json.dumps({
        "version": 1,
        "groups": [{"cid": OTHER, "alias": "测试群", "enabled": True,
                    "persona": "rose_butler"}],
        "bots": [{"key": "k", "name": "n", "cid": OTHER,
                  "token_env": "DZMM_BOT_TOKEN", "enabled": True}],
    }, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    env["DZMM_CONFIG"] = str(cfg)
    env["DZMM_BOT_TOKEN"] = "dummy"
    code = ("import bot, personas, json;"
            "print(json.dumps([personas.config_personas(),"
            "personas.persona_key_for({0!r})]))".format(OTHER))
    out = subprocess.run([sys.executable, "-c", code], cwd=str(root), env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, (out.stdout or "")[-300:] + (out.stderr or "")[-600:]
    got = _json.loads(out.stdout.strip().splitlines()[-1])
    assert got[0] == {OTHER: "rose_butler"}, "bot.py 要把配置里的人设注入进去"
    assert got[1] == "rose_butler", "该群最终人设必须是配置指定的那个"
