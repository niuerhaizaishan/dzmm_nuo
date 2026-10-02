"""DZMM 在线状态查询 — 通过 Socket.IO presence 机制.

用法：
    ~/.hermes/hermes-agent/venv/bin/python online_status.py [--watch 秒数]

输出：各聊天房间在线人数 + 私聊对方(糯糯)在线判断 + 实时上下线事件。

机制（前端源码逆向 + 实测确认）：
    1. token 来自 GET /api/auth/token（需 dzmm.ai cookie）
    2. Socket.IO 连接 wss://www.dzmm.ai/ws/matching/，auth={token}
    3. message:fetch-online-status → {success, onlineStatus: {chatroomId: onlineCount}, serverTime}
    4. presence:user-online / presence:user-offline → {chatroomId, userId, onlineCount, serverTime}
    在线是"房间维度"：用户打开哪个聊天窗口就在哪个房间 onlineCount+1。
"""
import asyncio, os, json, sys
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if p not in ("", _ROOT)]  # 避开项目自己的 socketio.py
import token_store, requests
import socketio
import aiohttp
import env_config

ORIGIN = env_config.get_dzmm_origin()
env_config.set_process_proxy_env()  # 设置/清空 HTTP(S)_PROXY + WS(S)_PROXY（直连时清空）

NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"   # 小小糯(bot 自己)
OWNER_UID = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"  # 糯糯(主人)
PRIVATE = "61801878-593a-4f3b-a17a-ace89dcddf73"    # 糯糯×小小糯私聊

KNOWN = {
    "61801878-593a-4f3b-a17a-ace89dcddf73": "糯糯×小小糯私聊",
    "bd7fe6a4-0065-4551-ad3d-94116dba8f9c": "妹子认证群",
    "93cc6c7b-ba9e-48f3-a106-3074483aebbd": "裳姐姐和她的骚贱小母狗",
    "c5269e31-00ca-4d4f-91d3-abfc7f6a3423": "糯糯文化",
}


def get_cookie():
    return token_store.get_cookie_dict()


def get_token(cookies):
    r = requests.get(ORIGIN + "/api/auth/token", cookies=cookies, timeout=15,
                     proxies=env_config.get_proxies())
    return r.json().get("access_token")


async def main(watch_sec: int = 0):
    token = get_token(get_cookie())
    http = aiohttp.ClientSession(trust_env=True)
    sio = socketio.AsyncClient(http_session=http)

    events = []

    @sio.on("presence:user-online")
    async def _on(d):
        events.append(("online", d))
        print(f"  ↑ 上线 userId={d.get('userId','')[:8]} 房间={KNOWN.get(d.get('chatroomId',''), d.get('chatroomId','')[:8])} onlineCount={d.get('onlineCount')}")

    @sio.on("presence:user-offline")
    async def _off(d):
        events.append(("offline", d))
        print(f"  ↓ 下线 userId={d.get('userId','')[:8]} 房间={KNOWN.get(d.get('chatroomId',''), d.get('chatroomId','')[:8])} onlineCount={d.get('onlineCount')}")

    try:
        await sio.connect(ORIGIN, socketio_path="/ws/matching",
                          transports=["websocket"], auth={"token": token},
                          headers={"Origin": ORIGIN, "User-Agent": "Mozilla/5.0 DZMM-Agent/1.0"})
        await asyncio.sleep(1)
        r = await sio.call("message:fetch-online-status", {})
        os_map = r.get("onlineStatus", {})
        print("=== 各房间在线人数 ===")
        for cid, cnt in os_map.items():
            print(f"  {KNOWN.get(cid, cid[:8]):<14} {cnt}")
        private_cnt = os_map.get(PRIVATE, 0)
        print()
        print("=== 糯糯在线判断 ===")
        # 私聊只有糯糯+小小糯。bot 的 Playwright 停在群聊页面不占私聊位，故 private_cnt 即糯糯。
        print(f"  私聊在线人数 = {private_cnt} → 糯糯{'在线' if private_cnt > 0 else '离线'}")
        if watch_sec > 0:
            print(f"\n=== 监听 {watch_sec}s（实时上下线）===")
            await asyncio.sleep(watch_sec)
    except Exception as e:
        print("[err]", str(e)[:200])
    finally:
        await sio.disconnect()
        await http.close()


if __name__ == "__main__":
    w = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--watch" else 0
    asyncio.run(main(w))
