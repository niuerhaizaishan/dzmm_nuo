# -*- coding: utf-8 -*-
"""P12-AZ 探针：账号身份**一条**到底能发多长？（Socket.IO 直发 vs Playwright 打字）

### 为什么还要探

P12-AN 夹逼出的「账号通道单条上限 200 字」是在 **Playwright 打字**那条路上测的
（`bot测试` 群：190 ✅ / 210 ❌）。而账号身份还有**第二条投递路**——
`tools/send_missed_recap.py` 用的 `socketio.DZMMSocket`（Socket.IO 轮询直发
`message:send`，`sent_by` = 账号 uid），那条路**真实落地过**（09-29 补发），
但**从来没测过字数**。

如果 Socket.IO 那条路不收 200 字限制，那"只能切两条"就不是平台限制，
而是我们只用了打字那条路 —— 茶水间的运势卡（257 字）就能像认证群一样**一条发完**。

### 怎么探（自检群 `bot测试`，12 人；本群就是 P12-AN 夹逼用的那个）

**对照组优先**：先发 190 字单行（`C190`）。它落地了才说明这一轮 Socket.IO 是通的，
后面 210/257 的成败才有意义；它没落地就重试整轮（这条路以
`Session ID unknown` 闻名，`send_missed_recap.py` 就是靠硬重试过的）。

| 探针 | 形状 | 用途 |
|---|---|---|
| `C190` | 单行 190 字 | 对照组（打字通道这条长度是 ✅ 的） |
| `B210` | 单行 210 字 | 打字通道的失败阈值之上 |
| `A257` | 9 行 257+ 字 | 真实运势卡的形状（emoji + 两条分隔线 + 末行点评） |

每条都打印 `handshake / connect / poll / send / ack` **原始回执**，
最后读回最近 30 条按 tag 匹配，打印**落地文本的真实长度**（看是否被截断）。

用法（容器内）：`python /app/probe_long_socket.py`
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
TAG = "P12AZ" + str(int(time.time()))[-5:]
UA = "Mozilla/5.0 DZMM-Agent/1.0"


def _attempt(origin, cid, uid, text, cookie):
    """抄一遍 `socketio.DZMMSocket._try_send`，但留下每一步的原始回执。"""
    ts = str(int(time.time() * 1000))
    s = requests.Session()
    s.proxies.update(env_config.get_proxies() or {})
    s.headers.update({"Cookie": cookie, "Origin": origin,
                      "Referer": origin + "/chat", "User-Agent": UA,
                      "Accept": "*/*"})
    out = {}
    try:
        hs = (origin + "/ws/matching/?EIO=4&transport=polling"
              + "&t=" + ts + "&timestamp=" + ts + "&syncMode=http")
        r = s.get(hs, timeout=15)
        out["hs"] = [r.status_code, r.text[:120]]
        sid = json.loads(re.search(r"0(\{.*?\})", r.text).group(1))["sid"]
        base = (origin + "/ws/matching/?EIO=4&transport=polling"
                + "&sid=" + sid + "&timestamp=" + ts + "&syncMode=http")
        r2 = s.post(base, data=b"40",
                    headers={"Content-Type": "text/plain;charset=UTF-8"},
                    timeout=15)
        out["connect"] = [r2.status_code, r2.text[:120]]
        # 长轮询：服务端没事就挂着，所以给足 30s（上一版 12s 超时后 sid 直接失效）
        try:
            r2b = s.get(base, timeout=30)
            out["poll"] = [r2b.status_code, r2b.text[:200]]
        except Exception as e:                                  # noqa: BLE001
            out["poll"] = ["ERR", str(e)[:90]]

        mid = str(uuid.uuid4())
        msg = {"message_id": mid, "chatroom_id": cid, "sent_by": uid,
               "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
               "content": {"type": "text", "text": text}}
        batch = "\x1e".join([
            "42" + json.dumps(["message:join-room", {"chatroomId": cid}],
                              ensure_ascii=False, separators=(",", ":")),
            "42" + json.dumps(["message:send", {"chatroomId": cid, "message": msg}],
                              ensure_ascii=False, separators=(",", ":")),
        ])
        r3 = s.post(base, data=batch.encode("utf-8"),
                    headers={"Content-Type": "text/plain;charset=UTF-8"},
                    timeout=25)
        out["send"] = [r3.status_code, r3.text[:300]]
        try:
            r4 = s.get(base, timeout=25)
            out["ack"] = [r4.status_code, r4.text[:300]]
        except Exception as e:                                  # noqa: BLE001
            out["ack"] = ["ERR", str(e)[:90]]
        out["payload"] = len(batch)
    except Exception as e:                                      # noqa: BLE001
        out["fatal"] = str(e)[:160]
    finally:
        s.close()
    return out


def _landed(cid, uid, text):
    """读回：这条到底进群了没有（返回落地文本，没落地返回 None）。"""
    try:
        for m in dzmm.get_messages(cid, 30) or []:
            t = (m.get("content") or {}).get("text") or ""
            if t and t[:12] == text[:12]:
                return t
    except Exception as e:                                      # noqa: BLE001
        print("   读回异常:", str(e)[:100])
    return None


def _probes():
    card = ("A257 " + TAG + " 🤖 🔮 墨莲 今日运势 83分（吉）\n"
            "————————————\n"
            "📜 命盘 →  本命—：土 | 名格：火 |  今日：丁未火（阴） → 本命受生、名格比和\n"
            "📅 宜社交、表白 ｜ 忌独处、争执 ｜ 冲牛\n"
            "🧮 命基55 + 名格8 + 本命15 − 阴阳3 + 人和8 = 83\n"
            "📈 💰财运78 · 💼事业89 · 💗感情88 · 🩺健康75\n"
            "🍀 幸运：绛红 · 7 · 正南\n"
            "————————————\n"
            "墨莲姐姐今天本命土受生、名格火比和，事业感情都旺旺的～"
            "就是健康75分垫底，忙起来别又忘了好好吃饭呀，小小糯心疼你♡")
    return [
        ("C190", "C190 " + TAG + " " + "啊" * 179),
        ("B210", "B210 " + TAG + " " + "啊" * 200),
        ("A257", card),
    ]


def main():
    origin = env_config.get_dzmm_origin().rstrip("/")
    cookie = dzmm.get_cookie_string()
    me = ID.NUO_UID
    print("origin =", origin, "| 账号 uid =", me)
    print("探针群 =", CID, "(bot测试) | tag =", TAG)
    print("群里", len(dzmm.get_members(CID) or {}), "人；我在名单里：",
          me in (dzmm.get_members(CID) or {}))
    print()

    results = {}
    for name, text in _probes():
        print("=" * 62)
        print("### {}：{} 字 / {} 行".format(name, len(text), text.count("\n") + 1))
        got = None
        for attempt in range(1, 5):
            r = _attempt(origin, CID, me, text, cookie)
            summary = " ".join(
                "{}={}".format(k, (r.get(k) or ["-", ""])[0])
                for k in ("hs", "connect", "poll", "send", "ack") if k in r)
            print("  第 {} 次：{}".format(attempt, summary))
            send_body = (r.get("send") or ["", ""])[1]
            if send_body and "Unknown" not in send_body and send_body.strip() not in ("", "ok"):
                print("    send body:", send_body[:200])
            time.sleep(2)
            got = _landed(CID, me, text)
            if got:
                break
            print("    （读回没看到，重试整轮）")
            time.sleep(2)
        results[name] = got
        if got:
            print("  ✅ 落地：{} 字 / {} 行（发出 {} 字）".format(
                len(got), got.count("\n") + 1, len(text)))
        else:
            print("  ❌ 4 轮都没落地")
        time.sleep(2)

    print()
    print("=" * 62)
    print("结论：")
    c = results.get("C190")
    if not c:
        print("  ⚠️ 连 190 字的对照组都没落地 ⇒ 这一趟 Socket.IO 本身是坏的，"
              "**不能**据此判断长消息行不行。")
    else:
        print("  对照组 190 字：✅（Socket.IO 这轮是通的）")
        for name in ("B210", "A257"):
            g = results.get(name)
            print("  {}：{}".format(
                name, "✅ 一条完整落地（{} 字）".format(len(g)) if g else "❌ 没落地"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
