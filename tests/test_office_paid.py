"""付费概率玩法：概率档位、金额解析、分账取整、续写上限、钱包幂等与补偿。

方案：docs/茶水间潜规则付费概率玩法方案-2026-11-05（屋主定稿）。
"""
import json
import os
import uuid

import pytest

from runtime import office_rules as rules
from runtime import office_wallet
from runtime.office_store import KV_FEE, KV_SPLIT, RuleError, Store


# ---------------------------------------------------------------- 概率与费用

@pytest.mark.parametrize("distance,higher,lower", [
    (0, 50, 50), (1, 60, 40), (2, 70, 30), (3, 80, 20),
    (4, 90, 10), (5, 95, 5), (6, 99, 1), (10, 99, 1),
])
def test_probability_tiers(distance, higher, lower):
    """发起者更高 / 更低 两个方向的档位，6 级及以上封顶 99/1。"""
    assert rules.base_probability(1 + distance, 1) == higher
    assert rules.base_probability(1, 1 + distance) == lower


@pytest.mark.parametrize("base,extra,expected", [
    (70, 40, 90.0), (50, 40, 70.0), (20, 50, 45.0),
    (80, 38, 99.0), (99, 0, 99.0), (80, 50, 99.0),   # 加满不破 99
])
def test_final_probability(base, extra, expected):
    assert rules.final_probability(base, extra) == expected


def test_extra_must_be_integer_within_range():
    assert rules.final_probability(50, -1) is None
    assert rules.final_probability(50, 51) is None
    assert rules.final_probability(50, 1.5) is None


@pytest.mark.parametrize("base,cap_amount", [(70, 50), (80, 38), (90, 18), (99, 0), (20, 50)])
def test_max_extra_for_cap(base, cap_amount):
    """把基础概率推到 99% 所需的最小加币；超过 50 就按 50。"""
    assert rules.max_extra_for(base) == cap_amount


def test_checkout_reports_cap_and_rejects_overpay():
    bill = rules.checkout(38, 80)
    assert bill["fee"] == 43 and bill["probability"] == 99.0
    with pytest.raises(rules.RuleProblem) as error:
        rules.checkout(40, 80)              # 高目标 3 级：38 就封顶，40 应被拒
    assert "38" in str(error.value)


def test_total_fee_is_base_plus_extra():
    assert rules.total_fee(0) == 5
    assert rules.total_fee(40) == 45


# ---------------------------------------------------------------- 金额解析

@pytest.mark.parametrize("tokens,extra,rest", [
    ([], 0, []),
    (["开会"], 0, ["开会"]),
    (["40"], 40, []),
    (["40", "开会", "时"], 40, ["开会", "时"]),
    (["0", "40分钟后碰面"], 0, ["40分钟后碰面"]),
])
def test_take_extra(tokens, extra, rest):
    got_extra, got_rest, problem = rules.take_extra(tokens)
    assert problem is None and got_extra == extra and got_rest == rest


def test_take_extra_rejects_over_50():
    extra, rest, problem = rules.take_extra(["51", "开会"])
    assert extra is None and problem and "50" in problem


def test_direction_digits_are_not_money():
    """方向里的数字不当金额（方案 §4.3）。"""
    extra, rest, _ = rules.take_extra(["50", "0点", "在会议室"])
    assert extra == 50 and rest == ["0点", "在会议室"]


# ---------------------------------------------------------------- 分账

@pytest.mark.parametrize("rel,fee,expected", [
    ("up", 45, 22), ("peer", 45, 18), ("down", 45, 13),
    ("up", 5, 2), ("peer", 5, 2), ("down", 5, 1),
])
def test_split_floors(rel, fee, expected):
    got = rules.split_amount(fee, rules.split_ratio(rel))
    assert got == expected
    assert got <= fee, "分账不可能超过本次支付"


def test_relation_keys_match_split_direction():
    """relation() 的 up/down 就是"发起者更高/更低"，和分账比例同一套键。"""
    assert rules.relation(5, 2) == "up" and rules.split_ratio("up") == 0.5
    assert rules.relation(2, 5) == "down" and rules.split_ratio("down") == 0.3
    assert rules.relation(3, 3) == "peer" and rules.split_ratio("peer") == 0.4


# ---------------------------------------------------------------- 钱包适配层

@pytest.fixture
def mock_wallet(tmp_path, monkeypatch):
    path = tmp_path / "wallet.json"
    monkeypatch.setenv("DZMM_OFFICE_WALLET", "mock")
    monkeypatch.setenv("DZMM_OFFICE_WALLET_MOCK", str(path))
    return office_wallet.MockWallet(str(path))


def test_unavailable_wallet_never_lets_anyone_play(monkeypatch):
    monkeypatch.delenv("DZMM_OFFICE_WALLET", raising=False)
    wallet = office_wallet.adapter()
    assert isinstance(wallet, office_wallet.UnavailableWallet)
    assert wallet.balance("a")["ok"] is False
    with pytest.raises(office_wallet.WalletUnavailable):
        wallet.spend("a", 5, "k1")
    assert office_wallet.available() is False


def test_mock_wallet_spend_is_idempotent(mock_wallet):
    mock_wallet.grant("a", 100, "seed")
    first = mock_wallet.spend("a", 45, "session:fee")
    again = mock_wallet.spend("a", 45, "session:fee")
    assert first["balance"] == 55 and again["balance"] == 55
    assert again.get("replayed") is True
    assert mock_wallet.balance("a")["balance"] == 55


def test_mock_wallet_refuses_insufficient(mock_wallet):
    mock_wallet.grant("a", 5, "seed")
    with pytest.raises(office_wallet.WalletRefused):
        mock_wallet.spend("a", 45, "session:fee")
    assert mock_wallet.balance("a")["balance"] == 5


def test_mock_wallet_lookup(mock_wallet):
    mock_wallet.grant("a", 10, "seed")
    assert mock_wallet.lookup("nope")["state"] == "absent"
    mock_wallet.spend("a", 5, "k")
    assert mock_wallet.lookup("k")["state"] == "applied"


# ---------------------------------------------------------------- 存储与账务

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "office.db")


def test_continue_limit_is_five(store):
    store.preference("tea", "b", True)
    sid, op = store.start("tea", "a", "b", {"id": "m1"}, [])
    assert store.ready(sid, op, "正文")
    assert store.dispatch(sid, op)
    store.delivered(sid, op, "mid-1")
    for index in range(rules.MAX_CONTINUES):
        _, cont_op = store.begin_continue("tea", "b", {"id": "c{}".format(index + 1)}, [])
        assert store.ready(sid, cont_op, "续写正文")
        assert store.dispatch(sid, cont_op)
        store.delivered(sid, cont_op, "mid-{}".format(index + 2))
    with pytest.raises(RuleError, match="续写次数"):
        store.begin_continue("tea", "b", {"id": "over"}, [])


def test_lost_session_cannot_continue(store):
    store.preference("tea", "b", True)
    sid, op = store.start("tea", "a", "b", {"id": "m1"}, [], outcome="lose")
    assert store.ready(sid, op, "失败的一瞬")
    assert store.dispatch(sid, op)
    store.delivered(sid, op, "mid", outcome="lose")
    assert store.get(sid)["status"] == "lost"
    with pytest.raises(RuleError, match="没有可以继续"):
        store.begin_continue("tea", "b", {"id": "c1"}, [])


def test_wallet_ops_are_recorded_and_pending_is_visible(store):
    sid, _ = store.start("tea", "a", "b", {"id": "m1"}, [], fee=45)
    store.wallet_record("{}:{}".format(sid, KV_FEE), "tea", sid, "fee", "a", 45, "pending")
    assert store.wallet_op("{}:{}".format(sid, KV_FEE))["state"] == "pending"
    assert len(store.wallet_pending(sid)) == 1
    store.wallet_update("{}:{}".format(sid, KV_FEE), "applied", "tx-1")
    assert store.wallet_pending(sid) == []
    assert store.wallet_op("{}:{}".format(sid, KV_FEE))["remote"] == "tx-1"


def test_split_owed_survives_for_compensation(store):
    sid, _ = store.start("tea", "a", "b", {"id": "m1"}, [], fee=45)
    store.wallet_record("{}:{}".format(sid, KV_SPLIT), "tea", sid, "split", "b", 22, "owed")
    pending = store.wallet_pending(sid)
    assert len(pending) == 1 and pending[0]["kind"] == "split" and pending[0]["amount"] == 22


def test_quota_release_only_touches_reserved(store):
    store.preference("tea", "b", True)
    sid, op = store.start("tea", "a", "b", {"id": "m1"}, [])
    assert store.status("tea", "a")["remaining"] == 0
    store.release_quota(sid)
    assert store.status("tea", "a")["remaining"] == 1
    store.end("tea", "a")                       # 关掉上一场再开新的
    # 已投递（quota 变 used）的会话不该被 release 撤回
    sid2, op2 = store.start("tea", "a", "b", {"id": "m2"}, [])
    assert store.ready(sid2, op2, "正文")
    assert store.dispatch(sid2, op2)
    store.delivered(sid2, op2, "mid")
    store.release_quota(sid2)
    assert store.status("tea", "a")["remaining"] == 0
