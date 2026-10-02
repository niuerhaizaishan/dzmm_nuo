# -*- coding: utf-8 -*-
"""P12-AZ 探针②：Socket.IO 握手的哪个变体是**稳**的？（顺带再验一次长消息）

### 背景

探针① 的结果：**273 字 / 9 行的运势卡形状，一条完整落地** ✅ ——
说明「账号通道 200 字上限」只属于**打字 UI**，账号的 Socket.IO API 那条路不收它。
但同一次运行里 9 次尝试只成 1 次，失败全是 `{"code":1,"message":"Session ID unknown"}`。
`socketio.DZMMSocket` 的注释也早就写了这条路"时好时坏"。

生产上要用它，就必须先知道**哪种握手顺序**能稳定拿到可用会话。

### 变体（每次都发一条 273 字的卡，落地了才算这个变体成立）

| 变体 | 顺序 |
|---|---|
| `cur` | hs → POST `40` → GET poll → POST batch（现行代码，`socketio.py:_try_send`） |
| `poll_first` | hs → **GET poll** → POST `40` → POST batch |
| `fresh_ts` | 同 `cur`，但**每个请求各自一个新 `t`**（现行代码全程复用握手那个 `t`） |
| `no_syncmode` | 同 `cur`，但**不带 `syncMode=http`** |
| `no_poll` | hs → POST `40` → POST batch（**不做**中间那次 GET poll） |
| `retry20` | 同 `cur`，但连接没成（connect=400）就**重新握手重来**，最多 20 次 |

判定一律靠**读回**（`dzmm.get_messages`），不信 HTTP 状态码。

用法（容器内）：`python /app/probe_socket_variants.py`
"""
import json
import re
import sys
import time
import uuid

sys.path.insert(0, "/app")

import requests                                                # noqa: E402
import env_config                                              # noqa: E402
import dzmm                                                    # noqa: E402
from runtime import identity as ID                              # noqa: E402

CID = "f71ef7ff-5faf-4e5d-9dd6-8e930c574de3"     # bot测试（自检群，12 人）
TAG = "P12BZ" + str(int(time.time()))[-5:]
UA = "Mozilla/5.0 DZMM-Agent/1.0"
ORIGIN = None
COOKIE = None
ME = None


def _sess():
    s = requests.Session()
    s.proxies.update(env_config.get_proxies() or {})
    s.headers.update({"Cookie": COOKIE, "Origin": ORIGIN,
                      "Referer": ORIGIN + "/chat", "User-Agent": UA,
                      "Accept": "*/*"})
    return s


def _base(sid, ts, syncmode=True):
    q = "" if not syncmode else "&syncMode=http"
    return (ORIGIN + "/ws/matching/?EIO=4&transport=polling&sid=" + sid
            + "&timestamp=" + ts + q)


def _hs(s, syncmode=True):
    ts = str(int(time.time() * 1000))
    q = "" if not syncmode else "&syncMode=http"
    url = (ORIGIN + "/ws/matching/?EIO=4&transport=polling&t=" + ts
           + "&timestamp=" + ts + q)
    r = s.get(url, timeout=15)
    sid = json.loads(re.search(r"0(\{.*?\})", r.text).group(1))["sid"]
    return sid, ts


def _batch(text):
    mid = str(uuid.uuid4())
    msg = {"message_id": mid, "chatroom_id": CID, "sent_by": ME,
           "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
           "content": {"type": "text", "text": text}}
    return "\x1e".join([
        "42" + json.dumps(["message:join-room", {"chatroomId": CID}],
                          ensure_ascii=False, separators=(",", ":")),
        "42" + json.dumps(["message:send", {"chatroomId": CID, "message": msg}],
                          ensure_ascii=False, separators=(",", ":")),
    ])


def _post(s, url, data, timeout=25):
    return s.post(url, data=data,
                  headers={"Content-Type": "text/plain;charset=UTF-8"},
                  timeout=timeout)


def v_cur(text):
    s = _sess()
    try:
        sid, ts = _hs(s)
        b = _base(sid, ts)
        c = _post(s, b, b"40", 15)
        try:
            s.get(b, timeout=20)
        except Exception:                                       # noqa: BLE001
            pass
        r = _post(s, b, _batch(text).encode("utf-8"))
        return "connect={} send={} {}".format(c.status_code, r.status_code,
                                              r.text[:60])
    finally:
        s.close()


def v_poll_first(text):
    s = _sess()
    try:
        sid, ts = _hs(s)
        b = _base(sid, ts)
        try:
            s.get(b, timeout=20)
        except Exception:                                       # noqa: BLE001
            pass
        c = _post(s, b, b"40", 15)
        r = _post(s, b, _batch(text).encode("utf-8"))
        return "connect={} send={} {}".format(c.status_code, r.status_code,
                                              r.text[:60])
    finally:
        s.close()


def v_fresh_ts(text):
    s = _sess()
    try:
        sid, _ts = _hs(s)
        ts2 = str(int(time.time() * 1000))
        b = _base(sid, ts2)
        c = _post(s, b, b"40", 15)
        try:
            s.get(b, timeout=20)
        except Exception:                                       # noqa: BLE001
            pass
        ts3 = str(int(time.time() * 1000))
        b2 = _base(sid, ts3)
        r = _post(s, b2, _batch(text).encode("utf-8"))
        return "connect={} send={} {}".format(c.status_code, r.status_code,
                                              r.text[:60])
    finally:
        s.close()


def v_no_syncmode(text):
    s = _sess()
    try:
        sid, ts = _hs(s, syncmode=False)
        b = _base(sid, ts, syncmode=False)
        c = _post(s, b, b"40", 15)
        try:
            s.get(b, timeout=20)
        except Exception:                                       # noqa: BLE001
            pass
        r = _post(s, b, _batch(text).encode("utf-8"))
        return "connect={} send={} {}".format(c.status_code, r.status_code,
                                              r.text[:60])
    finally:
        s.close()


def v_no_poll(text):
    s = _sess()
    try:
        sid, ts = _hs(s)
        b = _base(sid, ts)
        c = _post(s, b, b"40", 15)
        r = _post(s, b, _batch(text).encode("utf-8"))
        return "connect={} send={} {}".format(c.status_code, r.status_code,
                                              r.text[:60])
    finally:
        s.close()


def v_retry20(text):
    """连接没成就重新握手，最多 20 次——看"硬重试"能不能把它救回来。"""
    last = "（没试）"
    for i in range(1, 21):
        s = _sess()
        try:
            sid, ts = _hs(s)
            b = _base(sid, ts)
            c = _post(s, b, b"40", 15)
            last = "第{}次 connect={}".format(i, c.status_code)
            if c.status_code != 200:
                time.sleep(1.0)
                continue
            try:
                s.get(b, timeout=20)
            except Exception:                                   # noqa: BLE001
                pass
            r = _post(s, b, _batch(text).encode("utf-8"))
            last = "第{}次 connect=200 send={} {}".format(
                i, r.status_code, r.text[:40])
            time.sleep(2)
            if _landed(text):
                return last + " → 落地"
        finally:
            s.close()
    return last


def _card(mark):
    return ("{} {} 🤖 🔮 墨莲 今日运势 83分（吉）\n"
            "————————————\n"
            "📜 命盘 →  本命—：土 | 名格：火 |  今日：丁未火（阴） → 本命受生、名格比和\n"
            "📅 宜社交、表白 ｜ 忌独处、争执 ｜ 冲牛\n"
            "🧮 命基55 + 名格8 + 本命15 − 阴阳3 + 人和8 = 83\n"
            "📈 💰财运78 · 💼事业89 · 💗感情88 · 🩺健康75\n"
            "🍀 幸运：绛红 · 7 · 正南\n"
            "————————————\n"
            "墨莲姐姐今天本命土受生、名格火比和，事业感情都旺旺的～"
            "就是健康75分垫底，忙起来别又忘了好好吃饭呀，小小糯心疼你♡").format(mark, TAG)


def _landed(text):
    try:
        for m in dzmm.get_messages(CID, 30) or []:
            t = (m.get("content") or {}).get("text") or ""
            if t and t[:10] == text[:10]:
                return len(t)
    except Exception:                                           # noqa: BLE001
        pass
    return 0


def main():
    global ORIGIN, COOKIE, ME
    ORIGIN = env_config.get_dzmm_origin().rstrip("/")
    COOKIE = dzmm.get_cookie_string()
    ME = ID.NUO_UID
    print("tag =", TAG, "| group = bot测试 | uid =", ME[:8])
    variants = [("cur", v_cur), ("poll_first", v_poll_first),
                ("fresh_ts", v_fresh_ts), ("no_syncmode", v_no_syncmode),
                ("no_poll", v_no_poll), ("retry20", v_retry20)]
    results = {}
    for name, fn in variants:
        text = _card("V_" + name)
        print("=" * 62)
        print("### {}（{} 字 / {} 行）".format(name, len(text), text.count("\n") + 1))
        t0 = time.time()
        try:
            note = fn(text)
        except Exception as e:                                  # noqa: BLE001
            note = "异常 " + str(e)[:80]
        time.sleep(3)
        n = _landed(text)
        results[name] = n
        print("  回执：{}".format(note))
        print("  读回：{}（耗时 {}s）".format(
            "✅ 一条完整落地（{} 字）".format(n) if n else "❌ 没落地",
            round(time.time() - t0, 1)))
        time.sleep(2)
    print()
    print("=" * 62)
    print("汇总（273 字卡片，一条完整落地 = 该变体成立）：")
    for name, n in results.items():
        print("  {:<12} {}".format(name, "✅ {} 字".format(n) if n else "❌"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
