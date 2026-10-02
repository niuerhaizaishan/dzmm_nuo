"""P12-AY：两处"昨天和今天一个样"的收尾 + 一处假告警。

屋主 2026-09-30（接着 P12-AX）：
  1. "**（运势卡的幸运）也按天变**" —— 原来 `_WX_LUCK` 每个五行一个固定三元组，
     同一个人的"幸运颜色·数字·方位"永远一样。
  2. "**（进程告警）可以**（加复核）" —— 2026-09-30 09:19:59 巡检白喊了一次
     "双进程异常（2 个 bot.py 实例）"：那是部署重建的一瞬间（旧容器在退、
     新容器的 entrypoint 还要先跑 headless_relogin 才启动 bot.py），5 分钟后自愈。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import check_bot_health as H                                     # noqa: E402
from runtime import fortune as FT                                # noqa: E402


# ---------------------------------------------------------------- 1. 幸运按天变

def test_luck_rotates_daily():
    """隔天必然换一个（日序数每天 +1 取模），不会连着两天一样。"""
    prev = None
    for day in range(20, 28):
        d = "2026-09-{:02d}".format(day)
        luck = FT._fortune_calc("u-luck", "御褚", d, "")["luck"]
        assert luck != prev, "{} 的幸运和前一天一样：{}".format(d, luck)
        prev = luck


def test_luck_stays_in_its_element_options():
    """幸运仍要落在本五行的候选里（别串到别的五行去）。"""
    for name in ("御褚", "囡囡", "紫苑"):
        info = FT._fortune_calc("u-" + name, name, "2026-09-30", "")
        opts = FT._WX_LUCK[info["name_wx"]]
        assert tuple(info["luck"]) in tuple(tuple(o) for o in opts), info["luck"]


def test_luck_same_day_is_stable():
    a = FT._fortune_calc("u-luck2", "囡囡", "2026-09-30", "")["luck"]
    b = FT._fortune_calc("u-luck2", "囡囡", "2026-09-30", "")["luck"]
    assert a == b


# ---------------------------------------------------------------- 2. 进程告警复核

def test_process_count_is_rechecked_before_alerting(monkeypatch):
    """第一次数到 2 个 → 隔 5 秒复核变 1 个 ⇒ 按 1 个算（不告警）。"""
    seq = [(2, ["1", "2"]), (1, ["1"])]
    monkeypatch.setattr(H, "check_process", lambda: seq.pop(0))
    monkeypatch.setattr(H.time, "sleep", lambda s: None)      # 别真等
    count, pids = H._process_count_debounced()
    assert count == 1 and pids == ["1"]


def test_process_count_keeps_a_real_double_instance(monkeypatch):
    """复核还是 2 个 ⇒ 保持 2（该告警就告警）。"""
    monkeypatch.setattr(H, "check_process", lambda: (2, ["1", "2"]))
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    assert H._process_count_debounced()[0] == 2


def test_single_instance_skips_the_recheck(monkeypatch):
    """正常时不该多等 5 秒（每 5 分钟跑一次的巡检，别自己拖时间）。"""
    calls = []

    def _cp():
        calls.append(1)
        return 1, ["1"]

    monkeypatch.setattr(H, "check_process", _cp)

    def _boom(_s):
        raise AssertionError("单实例不该走复核分支")

    monkeypatch.setattr(H.time, "sleep", _boom)
    assert H._process_count_debounced() == (1, ["1"])
    assert len(calls) == 1


def test_recheck_constant_is_small():
    """复核等待必须很短（巡检每 5 分钟一次，别把节奏拖坏）。"""
    assert 1 <= H.PROCESS_RECHECK_SEC <= 15
