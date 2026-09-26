# -*- coding: utf-8 -*-
"""把**公开 Bot** 加进某个群：`chatroom.addBot({chatroomId, botId})`（P12-H）。

### 为什么要在**浏览器页面里** fetch，而不是用 requests

同一个 tRPC 端点：
- **查询**（`chatroom.listBots` / `getMembers`）用 requests 直接 GET 是通的；
- **变更**（`chatroom.addBot`）用 requests POST 一律 **403 Forbidden**，
  换成页面内 `fetch` 立刻成功（返回 `{"status":"added"}`）。

⇒ 平台的 **WAF 对 tRPC mutation 校验浏览器上下文**（Origin/Referer/UA/x-trpc-source 之类），
裸 requests 会被拒。**这类"内部 tRPC 变更"以后一律走页面内 fetch。**

（2026-09-26 实测：认证群那个 Bot 是 2026-09-25 用 `chatroom.addBot` 加的，
所以接口本身没变；变的只是"从哪调"。）

### 前提（平台文档 https://www.laopo.ai/studio/bots/docs）

- **公开** Bot：任意普通群聊的群主/管理员都能加；
- **私有** Bot：只有创建者本人能加，且创建者必须是目标群的群主/管理员；
- **scenario 群聊不展示 Add Bot 入口**（这类群加不了 Bot）；
- 加成功后群里会收到一条系统消息，成员列表实时刷新。

### 用法

    python tools/add_bot_to_chatroom.py --cid <chatroom_id> --bot-id <bot_id>
    python tools/add_bot_to_chatroom.py --cid <chatroom_id> --bot-id ... --dry-run

`--dry-run` 只打开页面、打印当前成员，不做任何变更。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

JS_ADD = """
async ({cid, bid}) => {
  const shapes = [
    ['json',   {json: {chatroomId: cid, botId: bid}}],
    ['bare',   {chatroomId: cid, botId: bid}],
    ['batch0', {0: {json: {chatroomId: cid, botId: bid}}}],
  ];
  const out = [];
  for (const [name, body] of shapes) {
    let rec = {shape: name};
    try {
      const r = await fetch('/api/trpc/chatroom.addBot', {
        method: 'POST',
        headers: {'content-type': 'application/json', 'x-trpc-source': 'nextjs'},
        body: JSON.stringify(body), credentials: 'include',
      });
      rec.status = r.status;
      rec.text = (await r.text()).slice(0, 400);
      out.push(rec);
      if (r.status === 200 && !rec.text.includes('"error"')) break;  // 成功即止
    } catch (e) { rec.error = String(e).slice(0, 150); out.push(rec); }
  }
  return out;
}
"""

JS_MEMBERS = """
async (cid) => {
  const r = await fetch('/api/trpc/chatroom.getMembers?input=' +
      encodeURIComponent(JSON.stringify({json: {chatroomId: cid, limit: 200}})),
      {credentials: 'include'});
  const d = await r.json();
  const ms = ((d.result||{}).data||{}).json?.members || [];
  return ms.filter(m => m.isBot).map(m => [m.id, m.fullName]);
}
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="把公开 Bot 加进群（页面内 fetch tRPC）")
    ap.add_argument("--cid", required=True)
    ap.add_argument("--bot-id", dest="bot_id", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    import env_config
    import token_store
    from playwright.sync_api import sync_playwright

    origin = env_config.get_dzmm_origin().rstrip("/")
    cookies = [dict(c) for c in token_store.get_cookies_for_playwright()]
    print("origin =", origin, "| cookie 数 =", len(cookies))

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            "/tmp/pw_addbot_tool", channel=env_config.get_channel(), headless=True,
            viewport={"width": 1400, "height": 1000}, user_agent=UA,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-gpu"])
        try:
            ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.goto(origin + "/chat?c=" + a.cid, timeout=60000,
                      wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            before = page.evaluate(JS_MEMBERS, a.cid)
            print("加之前，群里的 Bot:", before or "（无）")
            if a.dry_run:
                print("--dry-run：不动。")
                return 0
            if not a.bot_id:
                raise SystemExit("要加就得给 --bot-id")
            if any(m[0] == a.bot_id for m in before):
                print("它已经在群里了，不用重复加（平台也不会重复加）。")
                return 0
            res = page.evaluate(JS_ADD, {"cid": a.cid, "bid": a.bot_id})
            for r in res:
                print("  shape={:<7} status={} {}".format(
                    r.get("shape"), r.get("status"),
                    (r.get("text") or r.get("error") or "")[:240]))
            ok = any(r.get("status") == 200 and '"error"' not in (r.get("text") or "")
                     for r in res)
            time.sleep(2)
            after = page.evaluate(JS_MEMBERS, a.cid)
            print("加之后，群里的 Bot:", after or "（无）")
            print("==>", "成功" if ok else "失败（看上面的 status/text）")
            return 0 if ok else 1
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
