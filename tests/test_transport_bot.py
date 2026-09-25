"""官方 Bot 发送客户端单测（用假 session，不发真实请求）。"""
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.transport_bot import (      # noqa: E402
    BotApiAmbiguous, BotApiRejected, DzmmBotSender,
)

CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else (str(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    """记录请求，返回预设响应或抛预设异常。"""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, proxies=None):
        self.calls.append({"url": url, "json": json, "headers": headers,
                           "timeout": timeout, "proxies": proxies})
        if self.exc:
            raise self.exc
        return self.response


def make(response=None, exc=None, **kw):
    s = FakeSession(response, exc)
    return DzmmBotSender("https://www.example.invalid", "tok-123", session=s, **kw), s


# ---------------------------------------------------------------- 构造校验

def test_rejects_empty_token_or_origin():
    with pytest.raises(ValueError):
        DzmmBotSender("https://x.invalid", "")
    with pytest.raises(ValueError):
        DzmmBotSender("", "tok")


def test_request_shape():
    sender, session = make(FakeResponse(200, {"ok": True, "result": {"message_id": "m1"}}))
    assert sender.send_text(CID, "你好") == "m1"
    call = session.calls[0]
    assert call["url"] == "https://www.example.invalid/api/bot/send-message"
    assert call["json"] == {"chatroom_id": CID, "content": "你好"}
    assert call["headers"]["X-Bot-Token"] == "tok-123"


def test_origin_trailing_slash_stripped():
    s = DzmmBotSender("https://x.invalid/", "tok", session=FakeSession())
    assert s.origin == "https://x.invalid"


def test_proxy_is_applied():
    sender, session = make(
        FakeResponse(200, {"ok": True, "result": {"message_id": "m"}}),
        proxy="http://127.0.0.1:7890")
    sender.send_text(CID, "x")
    assert session.calls[0]["proxies"] == {
        "http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_no_proxy_means_none():
    sender, session = make(FakeResponse(200, {"ok": True, "result": {"message_id": "m"}}))
    sender.send_text(CID, "x")
    assert session.calls[0]["proxies"] is None


# ---------------------------------------------------------------- 成功

def test_success_returns_message_id():
    sender, _ = make(FakeResponse(200, {"ok": True, "result": {"message_id": "mid-1"}}))
    assert sender.send_text(CID, "hi") == "mid-1"


# ---------------------------------------------------------------- 明确拒绝（可回退）

def test_not_a_member_is_rejected():
    """实测形状：403 + error: Bot is not a member of this chatroom"""
    sender, _ = make(FakeResponse(403, {"ok": False, "error": "Bot is not a member of this chatroom"}))
    with pytest.raises(BotApiRejected):
        sender.send_text(CID, "hi")


def test_validation_error_is_rejected():
    sender, _ = make(FakeResponse(400, {"ok": False, "error": "Invalid request body"}))
    with pytest.raises(BotApiRejected):
        sender.send_text(CID, "hi")


def test_invalid_method_is_rejected():
    """实测：未知方法返回 200 + ok:false + 'Invalid method' → 确定未投递。"""
    sender, _ = make(FakeResponse(200, {"ok": False, "message": "Invalid method"}))
    with pytest.raises(BotApiRejected):
        sender.send_text(CID, "hi")


# ---------------------------------------------------------------- 不明确（禁止重发）

def test_timeout_is_ambiguous():
    sender, _ = make(exc=requests.Timeout("timed out"))
    with pytest.raises(BotApiAmbiguous):
        sender.send_text(CID, "hi")


def test_connection_error_is_ambiguous():
    sender, _ = make(exc=requests.ConnectionError("reset"))
    with pytest.raises(BotApiAmbiguous):
        sender.send_text(CID, "hi")


def test_server_error_is_ambiguous():
    sender, _ = make(FakeResponse(500, {"ok": False, "error": "boom"}))
    with pytest.raises(BotApiAmbiguous):
        sender.send_text(CID, "hi")


def test_non_json_is_ambiguous():
    sender, _ = make(FakeResponse(200, None, text="<html>502</html>"))
    with pytest.raises(BotApiAmbiguous):
        sender.send_text(CID, "hi")


def test_ok_true_without_message_id_is_ambiguous():
    """最危险的一种：ok=true 说明**很可能已经投递**，绝不能重发。"""
    sender, _ = make(FakeResponse(200, {"ok": True, "result": {}}))
    with pytest.raises(BotApiAmbiguous):
        sender.send_text(CID, "hi")


def test_non_dict_body_is_ambiguous():
    sender, _ = make(FakeResponse(200, ["unexpected"]))
    with pytest.raises(BotApiAmbiguous):
        sender.send_text(CID, "hi")


# ---------------------------------------------------------------- 能力边界

def test_has_no_private_or_recall_api():
    """官方 Bot 不支持私聊/撤回 —— 这两种方法**不该存在**（结构上防止误用）。"""
    sender, _ = make()
    for name in ("send_private", "send_to_user", "recall", "recall_message",
                 "send_direct", "delete_message"):
        assert not hasattr(sender, name), name
