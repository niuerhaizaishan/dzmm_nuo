"""HttpWallet 对接 DDZM 第三方集成接口（按 2026-11-05 接入文档）。

覆盖：余额、扣币/发币请求体、409/401/404/422/503 的语义、幂等键与重放查证。
"""
import io
import json
import urllib.error

import pytest

from runtime import office_wallet

KEY = "c" * 32


class _Response(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def make_opener(responses):
    """按顺序返回预设响应；元素是 dict（200）或 (status, body)。"""
    queue = list(responses)
    seen = []

    def opener(request, timeout=None):
        seen.append({"url": request.full_url, "method": request.get_method(),
                     "headers": dict(request.header_items()),
                     "body": json.loads(request.data.decode("utf-8")) if request.data else None})
        item = queue.pop(0)
        if isinstance(item, tuple):
            status, body = item
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            raise urllib.error.HTTPError(request.full_url, status, "error", {},
                                         io.BytesIO(raw))
        return _Response(item)

    opener.seen = seen
    return opener


@pytest.fixture
def wallet(monkeypatch):
    monkeypatch.setattr(office_wallet.time, "sleep", lambda *a: None)
    base = office_wallet.HttpWallet(base="https://example.invalid/api", key="test-key",
                                   opener=make_opener([]))
    return base


def _wallet(responses):
    return office_wallet.HttpWallet(base="https://example.invalid/api", key="test-key",
                                    opener=make_opener(responses))


def test_balance_uses_api_key_header():
    opener = make_opener([{"platform_id": "u1", "balance": 37}])
    w = office_wallet.HttpWallet(base="https://x/api", key="k-12345678", opener=opener)
    got = w.balance("u1")
    assert got == {"ok": True, "balance": 37}
    call = opener.seen[0]
    assert call["url"].endswith("/users/u1/balance") and call["method"] == "GET"
    assert call["headers"].get("X-api-key") == "k-12345678" or \
        call["headers"].get("X-Api-Key") == "k-12345678"


def test_balance_404_is_reported_without_raising():
    w = _wallet([(404, {"detail": "not found"})])
    got = w.balance("u1")
    assert got["ok"] is False and "不存在" in got["error"]


def test_spend_sends_documented_payload():
    opener = make_opener([{"ok": True, "amount": 45, "balance_after": 12}])
    w = office_wallet.HttpWallet(base="https://x/api", key="k-12345678", opener=opener)
    got = w.spend("u1", 45, "session-fee-0001", "茶水间潜规则首段")
    assert got["ok"] and got["balance"] == 12 and got["amount"] == 45
    body = opener.seen[0]["body"]
    assert body["platform_id"] == "u1" and body["amount"] == 45
    assert body["idempotency_key"] == "session-fee-0001"
    assert body["allow_partial"] is False
    assert 1 <= len(body["reason"]) <= 200
    assert opener.seen[0]["url"].endswith("/coins/deduct")


def test_insufficient_balance_raises_refused():
    w = _wallet([(409, {"detail": {"ok": False, "error": {"code": "insufficient_balance"},
                                 "balance": 3, "requested": 45}})])
    with pytest.raises(office_wallet.WalletRefused):
        w.spend("u1", 45, "session-fee-0002")


def test_idempotency_conflict_is_unavailable_not_refused():
    w = _wallet([(409, {"detail": {"ok": False,
                                  "error": {"code": "idempotency_conflict"}}})])
    with pytest.raises(office_wallet.WalletUnavailable):
        w.spend("u1", 5, "session-fee-0003")


@pytest.mark.parametrize("status,expected", [
    (401, office_wallet.WalletUnavailable),
    (422, office_wallet.WalletUnavailable),
    (404, office_wallet.WalletRefused),
])
def test_error_status_mapping(status, expected):
    w = _wallet([(status, {"detail": {"error": {"code": "x"}}})])
    with pytest.raises(expected):
        w.grant("u1", 5, "session-split-0001")


def test_transient_503_is_retried_with_same_key():
    opener = make_opener([(503, {"detail": "busy"}), {"ok": True, "amount": 22,
                                                      "balance_after": 30}])
    w = office_wallet.HttpWallet(base="https://x/api", key="k-12345678", opener=opener)
    got = w.grant("u1", 22, "session-split-0002")
    assert got["ok"] and got["balance"] == 30
    keys = [call["body"]["idempotency_key"] for call in opener.seen]
    assert keys == ["session-split-0002", "session-split-0002"], "重试必须复用同一个幂等键"


def test_short_idempotency_key_is_rejected_before_network():
    w = _wallet([])
    with pytest.raises(office_wallet.WalletUnavailable):
        w.spend("u1", 5, "short")


def test_lookup_replays_the_cached_request():
    opener = make_opener([{"ok": True, "amount": 5, "balance_after": 10},
                          {"ok": True, "amount": 5, "balance_after": 10}])
    w = office_wallet.HttpWallet(base="https://x/api", key="k-12345678", opener=opener)
    w.spend("u1", 5, "session-fee-0004")
    assert w.lookup("unknown-key")["state"] == "unknown"
    assert w.lookup("session-fee-0004")["state"] == "applied"
    assert opener.seen[-1]["body"]["idempotency_key"] == "session-fee-0004"


def test_missing_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("DZMM_INTEGRATION_API_KEY", raising=False)
    w = office_wallet.HttpWallet(base="https://x/api", key="")
    with pytest.raises(office_wallet.WalletUnavailable):
        w.spend("u1", 5, "session-fee-0005")
    assert w.balance("u1")["ok"] is False
