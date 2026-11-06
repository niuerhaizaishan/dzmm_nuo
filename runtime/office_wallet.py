"""摸鱼币钱包适配层：余额 / 扣币 / 发币。

真实接口（DDZM 第三方集成，2026-11-05 文档）：
    GET  /users/{platform_id}/balance                → {balance}
    POST /coins/deduct  {platform_id, amount, reason, idempotency_key, allow_partial}
    POST /coins/grant   {platform_id, amount, reason, idempotency_key}
    认证：X-Api-Key；资金操作按 idempotency_key 幂等（24h），409 可能是余额不足或幂等冲突。

三种后端（`DZMM_OFFICE_WALLET`）：
  未设置（默认） → UnavailableWallet：任何操作都明确拒绝，**不扣币、不抽取、不扣次数**
  mock           → MockWallet：本地 JSON 记账，只用于联调与测试
  http           → HttpWallet：打真实集成接口（需要 DZMM_INTEGRATION_API_KEY）

合同（四个方法）：
  balance(uid)                     -> {"ok": bool, "balance": int, "error": str}
  spend(uid, amount, key, reason)  -> {"ok": True, "balance": int, "amount": int, "tx": str}
  grant(uid, amount, key, reason)  -> 同上
  lookup(key)                      -> {"ok": True, "state": "applied"/"absent"/"unknown"}
扣币/发币在**响应不明**时用同一个 key 重试（方案 §11「不换新键」），绝不变更幂等键。
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

DEFAULT_MOCK_PATH = "/app/data/office_wallet_mock.json"
DEFAULT_BASE = "https://49.234.20.159/api/integration"
MAX_REASON = 200
MIN_KEY, MAX_KEY = 8, 128
_LOCK = threading.Lock()
_RETRY_STATUS = (429, 503)
_RETRY_TIMES = 3


class WalletUnavailable(RuntimeError):
    """钱包没接上 / 调不通 —— 调用方必须按「不扣币、不抽取」处理。"""


class WalletRefused(RuntimeError):
    """业务拒绝（余额不足、员工不存在等）：明确不扣款。"""


class _Adapter:
    def balance(self, uid):
        raise NotImplementedError

    def spend(self, uid, amount, key, reason=""):
        raise NotImplementedError

    def grant(self, uid, amount, key, reason=""):
        raise NotImplementedError

    def lookup(self, key):
        raise NotImplementedError


class UnavailableWallet(_Adapter):
    """默认后端：没配就明确拒绝，绝不放行白嫖。"""

    reason = "摸鱼币接口还没接上（需要 DZMM_INTEGRATION_API_KEY）"

    def balance(self, uid):
        return {"ok": False, "error": self.reason}

    def spend(self, uid, amount, key, reason=""):
        raise WalletUnavailable(self.reason)

    def grant(self, uid, amount, key, reason=""):
        raise WalletUnavailable(self.reason)

    def lookup(self, key):
        return {"ok": False, "state": "unknown", "error": self.reason}


class MockWallet(_Adapter):
    """本地 JSON 记账：只给联调/测试用，绝不作为生产余额。"""

    def __init__(self, path=None):
        self.path = path or os.environ.get("DZMM_OFFICE_WALLET_MOCK") or DEFAULT_MOCK_PATH

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError
        except Exception:                                      # noqa: BLE001
            data = {}
        data.setdefault("balances", {})
        data.setdefault("ops", {})
        return data

    def _write(self, data):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)

    def balance(self, uid):
        with _LOCK:
            data = self._read()
        return {"ok": True, "balance": int(data["balances"].get(str(uid), 0))}

    def spend(self, uid, amount, key, reason=""):
        amount = int(amount)
        if amount <= 0:
            raise WalletRefused("金额必须大于 0")
        with _LOCK:
            data = self._read()
            done = data["ops"].get(str(key))
            if done:
                return {"ok": True, "balance": int(done["balance"]), "tx": done["tx"],
                        "amount": abs(int(done["amount"])), "replayed": True}
            bal = int(data["balances"].get(str(uid), 0))
            if bal < amount:
                raise WalletRefused("余额不足：现有 {} 币，需要 {} 币".format(bal, amount))
            bal -= amount
            tx = "mock-" + str(key)
            data["balances"][str(uid)] = bal
            data["ops"][str(key)] = {"uid": str(uid), "amount": -amount,
                                     "balance": bal, "tx": tx}
            self._write(data)
        return {"ok": True, "balance": bal, "tx": tx, "amount": amount}

    def grant(self, uid, amount, key, reason=""):
        amount = int(amount)
        if amount <= 0:
            return {"ok": True, "balance": self.balance(uid)["balance"], "tx": "", "amount": 0}
        with _LOCK:
            data = self._read()
            done = data["ops"].get(str(key))
            if done:
                return {"ok": True, "balance": int(done["balance"]), "tx": done["tx"],
                        "amount": int(done["amount"]), "replayed": True}
            bal = int(data["balances"].get(str(uid), 0)) + amount
            tx = "mock-" + str(key)
            data["balances"][str(uid)] = bal
            data["ops"][str(key)] = {"uid": str(uid), "amount": amount,
                                     "balance": bal, "tx": tx}
            self._write(data)
        return {"ok": True, "balance": bal, "tx": tx, "amount": amount}

    def lookup(self, key):
        with _LOCK:
            data = self._read()
        done = data["ops"].get(str(key))
        if not done:
            return {"ok": True, "state": "absent"}
        return {"ok": True, "state": "applied", "tx": done["tx"]}


class HttpWallet(_Adapter):
    """DDZM 第三方集成接口（摸鱼币）。

    资金操作只有在**明确成功**时才返回；余额不足/员工不存在抛 WalletRefused，
    网络或服务异常抛 WalletUnavailable（调用方按"未扣成"处理，绝不白玩）。
    """

    def __init__(self, base=None, key=None, timeout=8, opener=None):
        self.base = (base or os.environ.get("DZMM_INTEGRATION_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.key = key if key is not None else os.environ.get("DZMM_INTEGRATION_API_KEY", "")
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self._seen = {}          # key -> 最近一次请求（响应不明时按原样重放）

    # ---- 底层 ----
    def _http(self, method, path, payload=None):
        if not self.key:
            raise WalletUnavailable("缺少 DZMM_INTEGRATION_API_KEY")
        url = self.base + path
        headers = {"X-Api-Key": self.key, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        return self._opener(request, timeout=self.timeout)

    def _decode(self, response):
        raw = response.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            raise WalletUnavailable("接口返回了非 JSON 内容") from None

    @staticmethod
    def _error_code(body):
        detail = (body or {}).get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            error = detail.get("error")
            if isinstance(error, dict):
                return str(error.get("code") or ""), detail
            return str(detail.get("code") or ""), detail
        return "", {}

    def _call(self, method, path, payload=None):
        """执行一次调用；429/503/网络异常用**同一个 payload（同一个 key）**重试。"""
        last = ""
        for attempt in range(_RETRY_TIMES):
            try:
                with self._http(method, path, payload) as response:
                    return self._decode(response)
            except urllib.error.HTTPError as error:
                body = None
                try:
                    body = self._decode(error)
                except WalletUnavailable:
                    body = None
                code, detail = self._error_code(body)
                status = getattr(error, "code", 0)
                if status == 401:
                    raise WalletUnavailable("集成密钥无效（401），请联系管理员") from None
                if status == 404:
                    raise WalletRefused("员工不存在（404），请先入职或核对 UID") from None
                if status == 409:
                    if code == "idempotency_conflict":
                        raise WalletUnavailable("幂等键冲突（409），这笔需要人工核对") from None
                    balance = (detail or {}).get("balance")
                    raise WalletRefused(
                        "余额不足" if code == "insufficient_balance" or balance is not None
                        else "请求被拒（409）") from None
                if status == 422:
                    raise WalletUnavailable("参数被拒（422），请检查金额与幂等键") from None
                if status in _RETRY_STATUS and attempt + 1 < _RETRY_TIMES:
                    last = "接口 {}，退避重试".format(status)
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise WalletUnavailable(last or "接口不可用：HTTP {}".format(status)) from None
            except (urllib.error.URLError, OSError) as error:
                last = "网络异常：" + type(error).__name__
                if attempt + 1 < _RETRY_TIMES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise WalletUnavailable(last) from None
        raise WalletUnavailable(last or "接口不可用")

    @staticmethod
    def _reason(text, fallback):
        value = " ".join(str(text or fallback).split())[:MAX_REASON]
        return value or fallback

    @staticmethod
    def _key(key):
        value = str(key or "")
        if not MIN_KEY <= len(value) <= MAX_KEY:
            raise WalletUnavailable("幂等键长度必须在 {}~{} 之间".format(MIN_KEY, MAX_KEY))
        return value

    # ---- 合同 ----
    def balance(self, uid):
        try:
            data = self._call("GET", "/users/{}/balance".format(uid))
        except WalletRefused as exc:
            return {"ok": False, "error": str(exc)}
        except WalletUnavailable as exc:
            return {"ok": False, "error": str(exc)}
        balance = data.get("balance")
        if not isinstance(balance, int) or isinstance(balance, bool):
            return {"ok": False, "error": "接口没有返回余额"}
        return {"ok": True, "balance": balance}

    def spend(self, uid, amount, key, reason=""):
        payload = {"platform_id": str(uid), "amount": int(amount),
                   "reason": self._reason(reason, "茶水间潜规则消费"),
                   "idempotency_key": self._key(key), "allow_partial": False}
        self._seen[str(key)] = ("spend", payload)
        data = self._call("POST", "/coins/deduct", payload)
        if not data.get("ok"):
            raise WalletUnavailable("扣币失败")
        actual = data.get("actual_amount", data.get("amount", int(amount)))
        return {"ok": True, "balance": data.get("balance_after"),
                "amount": int(actual or amount), "tx": str(key)}

    def grant(self, uid, amount, key, reason=""):
        payload = {"platform_id": str(uid), "amount": int(amount),
                   "reason": self._reason(reason, "茶水间潜规则分账"),
                   "idempotency_key": self._key(key)}
        self._seen[str(key)] = ("grant", payload)
        data = self._call("POST", "/coins/grant", payload)
        if not data.get("ok"):
            raise WalletUnavailable("发币失败")
        return {"ok": True, "balance": data.get("balance_after"),
                "amount": int(data.get("amount") or amount), "tx": str(key)}

    def lookup(self, key):
        """接口没有查询端点 —— 用**原样重放**同一请求来查证（幂等保证不重复记账）。"""
        cached = self._seen.get(str(key))
        if not cached:
            return {"ok": True, "state": "unknown"}
        _kind, payload = cached
        path = "/coins/deduct" if "allow_partial" in payload else "/coins/grant"
        try:
            data = self._call("POST", path, payload)
        except WalletRefused:
            return {"ok": True, "state": "absent"}
        except WalletUnavailable as exc:
            return {"ok": False, "state": "unknown", "error": str(exc)}
        if data.get("ok"):
            return {"ok": True, "state": "applied", "tx": str(key)}
        return {"ok": True, "state": "unknown"}


def adapter():
    """按环境变量选后端。默认 UnavailableWallet（不扣币、不抽取）。"""
    mode = (os.environ.get("DZMM_OFFICE_WALLET") or "").strip().lower()
    if mode == "mock":
        return MockWallet()
    if mode == "http":
        return HttpWallet()
    return UnavailableWallet()


def available():
    return not isinstance(adapter(), UnavailableWallet)
