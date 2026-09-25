"""runtime.settings 的单测：配置校验必须 fail fast。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.settings import (                       # noqa: E402
    DEFAULT_GROUP_CID,
    Settings,
)

BASE = {
    "DZMM_BOT_TOKEN": "tok-123",
    "DZMM_BOT_CID": DEFAULT_GROUP_CID,
}


def test_defaults_are_sane():
    s = Settings.from_environment({})
    assert s.group_cid == DEFAULT_GROUP_CID
    assert s.send_transport == "legacy"
    assert s.bot is None
    assert s.bot_enabled is False          # 没 token → 通道关闭


def test_bot_enabled_requires_both_token_and_switch():
    s = Settings.from_environment({**BASE})
    assert s.bot is not None
    assert s.bot_enabled is False          # 有 token 但开关还是 legacy
    s2 = Settings.from_environment({**BASE, "SEND_TRANSPORT": "botapi"})
    assert s2.bot_enabled is True


def test_bot_channel_disabled_without_token():
    s = Settings.from_environment({"SEND_TRANSPORT": "botapi"})
    assert s.bot is None
    assert s.bot_enabled is False


def test_bad_transport_rejected():
    with pytest.raises(ValueError, match="SEND_TRANSPORT"):
        Settings.from_environment({"SEND_TRANSPORT": "botapi_bd7fe6a4"})


def test_bot_cid_must_match_target_group():
    """防止配错群导致误发——这是最贵的一种错。"""
    with pytest.raises(ValueError, match="不一致"):
        Settings.from_environment({**BASE, "DZMM_BOT_CID": "some-other-group"})


def test_outbound_concurrency_bounds():
    with pytest.raises(ValueError, match="1..16"):
        Settings.from_environment({"DZMM_OUTBOUND_CONCURRENCY": "0"})
    with pytest.raises(ValueError, match="1..16"):
        Settings.from_environment({"DZMM_OUTBOUND_CONCURRENCY": "17"})
    assert Settings.from_environment({"DZMM_OUTBOUND_CONCURRENCY": "8"}).outbound_concurrency == 8


def test_origin_trailing_slash_stripped():
    s = Settings.from_environment({"DZMM_ORIGIN": "https://www.dzmm.ai/"})
    assert s.origin == "https://www.dzmm.ai"


def test_env_var_always_wins_over_provided_dict():
    """传 dict 时仍应读取 dict（而不是 os.environ），保证可测。"""
    s = Settings.from_environment({"DZMM_GROUP_CID": "g1"})
    assert s.group_cid == "g1"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as e:                       # noqa: BLE001
                failed += 1
                print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print("失败" if failed else "全部通过")
    raise SystemExit(1 if failed else 0)
