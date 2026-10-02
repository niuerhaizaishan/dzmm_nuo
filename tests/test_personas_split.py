"""P3-6 的守卫测试：personas.py 拆成 personas/ 包（2026-10-01）。

拆分方案（对计划书的两处偏离，均为保接缝）：
1. **函数与状态全部留在 `__init__.py`**，没有 core.py —— 十几个测试文件靠
   `monkeypatch.setattr(P, "_CONFIG_PERSONA", …)` 打包属性，函数若搬去 core.py
   读的就是 core 自己的全局，patch 包属性会失效（P3-4 `_TURNS` 族同款教训）。
2. 数据拆成 4 个**纯数据模块**：nuo / rose_butler（计划书叫 weisi.py，实际
   人设 key 是 rose_butler）/ ruojin / registry；没有单独的 defaults.py
   （nuo 的 extra 为空，默认话术就是 nuo 的话术，一个文件两件事）。

这批测试钉住：接口不变、数据逐字不变、纯数据纪律、monkeypatch 接缝活着。
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import personas as P                                            # noqa: E402
from personas import nuo as P_nuo                               # noqa: E402
from personas import registry as P_reg                          # noqa: E402
from personas import rose_butler as P_rose                      # noqa: E402
from personas import ruojin as P_ruo                            # noqa: E402

PKG = ROOT / "personas"
OLD_PY = ROOT / "personas.py"

# ---------------------------------------------------------------- ① 结构


def test_package_exists_and_old_file_is_gone():
    assert PKG.is_dir()
    assert not OLD_PY.exists(), "personas.py 与 personas/ 包不能共存（同名冲突）"


def test_package_files():
    got = sorted(p.name for p in PKG.glob("*.py"))
    assert got == ["__init__.py", "nuo.py", "registry.py", "rose_butler.py",
                   "ruojin.py"], got


# ---------------------------------------------------------------- ② 接口（计划要求的断言）

def test_personas_import():
    assert set(P.PERSONAS) == {"nuo", "rose_butler", "ruojin"}


def test_personas_interface():
    for fn in ("fill", "p", "persona_for", "phrases_for",
               "effective_persona", "set_cid", "current_cid",
               "set_config_personas", "config_personas",
               "set_content_overrides", "content_overrides",
               "known_phrase_keys", "health", "summary", "describe_persona",
               "is_default", "apply_persona", "me", "me_full",
               "orientation", "is_straight", "all_names", "phrase_is_blank"):
        assert callable(getattr(P, fn, None)), fn
    assert callable(P.use_overrides)


def test_hotload_surface_intact():
    """runtime/hotload.py 靠这四个函数做热加载 —— 包拆分不能动它们。"""
    for fn in ("set_content_overrides", "set_config_personas",
               "content_overrides", "config_personas"):
        assert callable(getattr(P, fn, None)), fn


# ---------------------------------------------------------------- ③ 数据逐字不变

def test_registry_assembles_the_three_phrase_tables():
    assert P.PHRASES["nuo"] is P_nuo.NUO_PHRASES
    assert P.PHRASES["rose_butler"] is P_rose.ROSE_PHRASES
    assert P.PHRASES["ruojin"] is P_ruo.RUOJIN_PHRASES


def test_persona_extras_live_in_their_own_modules():
    assert P.PERSONAS["rose_butler"]["extra"] is P_rose.ROSE_BUTLER_EXTRA
    assert P.PERSONAS["ruojin"]["extra"] is P_ruo.RUOJIN_EXTRA
    assert P.PERSONAS["nuo"]["extra"] == ""


def test_room_and_rose_data_live_in_rose_butler_module():
    for name in ("ROOM_GREET_TEMPLATE", "ROOM_GHOST_TEXT", "ROOM_GHOST_MAX_H",
                 "ROSE_SIGNS", "ROSE_SIGN_EMOJI", "ROOM_LEWD_WORDS"):
        assert getattr(P_rose, name, None) is not None, name
        assert getattr(P, name) is getattr(P_rose, name), name


def test_labels_and_rooms_live_in_registry():
    assert P.PHRASE_LABELS is P_reg.PHRASE_LABELS
    assert P.ROOMS is P_reg.ROOMS
    assert P.CID_PERSONA is P_reg.CID_PERSONA
    assert P.ROSE_CID in P.ROOMS


def test_canary_phrases_unchanged():
    """逐字抽查三条（默认 / 若瑾 / 玫瑰房各一）—— 搬家不许顺嘴改字。"""
    assert P.PHRASES["nuo"]["mem_saved"] == "记好啦～小小糯会记住的～♡"
    assert P.PHRASES["ruojin"]["chat_decor"] == "❤️"
    assert P.PHRASES["rose_butler"]["spice_emoji"] == ""
    assert "{tone}" not in P.RUOJIN_EXTRA, "口头零件必须已注入"


# ---------------------------------------------------------------- ④ 纯数据纪律

def test_data_modules_import_nothing():
    """nuo / rose_butler / ruojin 必须零 import（同 prompt_defaults.PD 的纪律）：
    任何环境（缺 runtime、缺依赖）都能加载，且不会反过来牵住别的东西。"""
    for mod in (P_nuo, P_rose, P_ruo):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        bad = [n for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert bad == [], "{} 里有 import：{}".format(mod.__name__, bad)


def test_registry_imports_only_from_the_package():
    tree = ast.parse((PKG / "registry.py").read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            # 相对导入（level>=1）= 包内；绝对导入必须点名 personas.*
            assert n.level >= 1 or (n.module or "").startswith("personas."), \
                "registry 只许 import 包内模块：{}".format(n.module)
        elif isinstance(n, ast.Import):
            pytest.fail("registry.py 不许 import 外部模块")


# ---------------------------------------------------------------- ⑤ monkeypatch 接缝

def test_config_persona_seam_patches_the_package(monkeypatch):
    """9 个测试文件的接缝：patch 包属性必须**真的生效**（函数读的是 __init__ 全局）。

    这条是"函数为什么留在 __init__.py 而不是 core.py"的回归钉。
    """
    monkeypatch.setattr(P, "_CONFIG_PERSONA", {"cid-x": "ruojin"})
    assert P.persona_key_for("cid-x") == "ruojin"
    assert P.persona_key_for("nobody") == "nuo"


def test_override_seams_patch_the_package(monkeypatch):
    monkeypatch.setattr(P, "CONTENT_OVERRIDES", {"nuo": {"name_short": "试"}})
    monkeypatch.setattr(P, "PHRASE_OVERRIDES", {"nuo": {"mem_saved": "改了"}})
    eff = P.effective_persona("nuo")
    assert eff["name_short"] == "试"
    assert P.phrases_for("nuo")["mem_saved"] == "改了"
