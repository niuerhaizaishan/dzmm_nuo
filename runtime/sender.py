"""发送引擎（P3-7，2026-10-01 自 bot.py 原样抽出）。

`class Sender`：Playwright 常驻页面的账号通道发送（打字 + Socket.IO 长消息 +
读回验证 + 回退切分 + token 收割/刷新 + 验证码联动），与 `get_cached_cookie` /
`_split_for_account` / ACCOUNT_MSG_MAX_CHARS / SIO_LONG_* / TOKEN_HARVEST_SEC
同族搬入。逐字搬迁纪律与 3-1~3-4 一致：逻辑不变，只动「住在哪」。

bot 侧经 import 区同名 re-export（`bot.Sender` 与这里是**同一个类对象**，
`monkeypatch.setattr(bot.Sender, "方法", …)` 不受搬家影响）。

共享状态归宿（P3-7 决策）：
- `_LIVE_SIO`：真身在本模块；bot `main()` 唯一写点改 `_sender_mod._LIVE_SIO = …`；
- `_captcha_active`：真身在 `runtime/ctx.py`（进程级旗标），本类只读；
- `GROUPS`：页缓存分组键改**构造注入**（`groups=`，bot 三个构造点传 GROUPS），
  类级默认空集 = `__new__` 出来的测试实例全部按 scratch 页处理。

bot._feat_on 的同款安全封装（fail-open + 记日志）在本模块有一份 `_gate`
（调用点写 `_feat_on`，让 test_feature_registry 的静态扫描数到这里的接线）。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import env_config
import token_store
from dzmm import get_messages
from runtime import ctx as _ctx
from runtime import session_obs as _session_obs
from runtime.ctx import PRIMARY_CID
from runtime.identity import is_self, NUO_UID
from runtime.obs import log
from wx_alert import send_weixin

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = env_config.get_dzmm_origin()
import personas as _personas               # P3-7：人设层（玫瑰房星象卡行数上限等）

# 常驻页面池上限（LRU 淘汰）；1.8G 的机器上别开太多 chromium 页
PAGE_CACHE_MAX = 3
PAGE_MAX_AGE_SEC = 7200                    # 页面存活超 2 小时 → 发送前重载（P12 惰性重载）


def _gate(cid: str, key: str) -> bool:
    """功能开关的安全封装 —— 与 bot._feat_on 同一份语义（fail-open + 记日志）。"""
    try:
        from runtime import features as _features
        return _features.on(cid or "", key)
    except Exception as e:                                     # noqa: BLE001
        log("features: 判定失败 key=" + str(key) + " " + str(e)[:60])
        return True


_feat_on = _gate  # 字面别名：类内调用点写 `_feat_on(...)`（与 joins/room/at_chat 同款）

# Cookie 缓存（30s 刷新一次，避免每次读 token JSON）
_cookie_cache = {"cookie_str": "", "ts": 0.0}


def get_cached_cookie() -> str:
    """缓存 cookie 字符串，30s 刷新一次."""
    if time.time() - _cookie_cache["ts"] < 30 and _cookie_cache["cookie_str"]:
        return _cookie_cache["cookie_str"]
    _cookie_cache["cookie_str"] = token_store.get_cookie_string()
    _cookie_cache["ts"] = time.time()
    return _cookie_cache["cookie_str"]


# 账号通道（Playwright 打字发送）单条字数上限。
# 实测（2026-09-29，`bot测试` 群夹逼）：**190 字能发、210 字发不出** →
# 平台的限制是 200 字，**超了会静默丢弃**（前端不接受，Enter 无效）。
# 我们取 190 留余量（emoji/全角都算 1 个 Python 字符，但"🤖 "前缀也算在内）。
ACCOUNT_MSG_MAX_CHARS = 190


def _split_for_account(text: str, limit: int = ACCOUNT_MSG_MAX_CHARS) -> list:
    """把一条长消息切成多段，每段 ≤ `limit` 字（P12-AN）。

    为什么要切：账号通道单条上限 **200 字**（实测 190 ✅ / 210 ❌），超限**静默失败**
    —— 平台前端直接不收，Enter 无效，我们这边只看到"假成功检测"。
    而官方 Bot API 那条路是 10000 字，于是"长回复"在 Bot 通道正常、**在账号通道全灭**。
    2026-09-29 现场：顶层茶水间（没有 Bot，只走账号）的缺勤回顾 400+ 字，一条都发不出去。

    切法（尽量不破坏可读性）：
      · **优先在换行处切**（每段自成一块），单行本身超长时才按字数硬切；
      · 按原顺序返回，不丢字符。
    """
    limit = max(1, int(limit))
    if not text:
        return [text]
    parts, cur = [], ""
    for raw_line in text.split("\n"):
        chunks = [raw_line[i:i + limit] for i in range(0, len(raw_line), limit)] or [""]
        for chunk in chunks:
            candidate = chunk if not cur else cur + "\n" + chunk
            if len(candidate) <= limit:
                cur = candidate
            else:
                if cur:
                    parts.append(cur)
                cur = chunk
    if cur:
        parts.append(cur)
    return parts or [text]


# P12-AZ（2026-09-30）：账号身份其实有**两条**投递路。
#
#   ① Playwright 打字（上面那个 200 字上限就是它的）
#   ② Socket.IO API（`socketio.DZMMSocket`，`sent_by` 同样是账号 uid）
#
# ②**不受** 200 字限制：实测（`bot测试` 群，2026-09-30）**273 / 278 字、9 行的整张
# 运势卡一条完整落地**。代价是它"时好时坏"——失败长这样：
# `connect=400 {"code":1,"message":"Session ID unknown"}`，而且**connect 失败时
# `send` 有时也返回 `200 ok`、群里什么都没有**（P12-AN 那个"假成功"就是它）。
#
# 所以长消息的策略是：**先试 ②**（成了就是一条，和认证群的 Bot 通道观感一致），
# 每轮都**读回**，读不到就重新握手再来；预算用完还没成就退回 ①（切 ≤190 字多条）。
SIO_LONG_BUDGET_SEC = 25.0      # ② 最多占用多久（别把一条回复拖成一分多钟）
SIO_LONG_MAX_TRIES = 8          # ② 最多重新握手几次（实测第 4~6 次成功过）
SIO_LONG_READBACK = 20          # ② 的读回窗口（茶水间 792 人，5 条窗口不够）

# P12-BK：把浏览器里的 token 抄回 JSON 的节流（秒）。
# SPA 会自己刷新并**轮换** refresh_token —— 不抄回来的话我们手里那份就是旧的。
TOKEN_HARVEST_SEC = 60

# 读消息用的那条长连接（`sio_subscribe.SioSubscriber`），由 `main()` 装上。
# P12-AZ 让它顺手也能发 —— 那条连接**不用握手**，不会撞 `Session ID unknown`。
_LIVE_SIO = None



class Sender:
    # P3-7：页缓存分组键的类级默认（__new__ 出来的测试实例用；生产构造注入 GROUPS）
    _known_groups = frozenset()
    """常驻 Playwright 浏览器：每个群一个常驻 page，发送时不再来回 goto。

    （2026-09-14 提速）原来每次给非主群发消息都要 goto 目标群 → 发完再 goto 回主群，
    两次整页导航约 4~6 秒，占一次回复耗时的大头。改成每群一个常驻页面后，
    发送只剩「输入 + 回车 + 读回验证」，从 8~17 秒降到 2~4 秒。
    """

    def __init__(self, profile_dir: str = "pw_profile", primary_cid: str = PRIMARY_CID, groups=()):
        self.ctx = None
        self.page = None
        self.current_cid = None
        self._known_groups = frozenset(groups or ())   # P3-7：页缓存分组键
        self.profile_dir = profile_dir
        self.primary_cid = primary_cid
        self.pages = {}  # cid -> {"page":..., "ts":..., "loaded":..., "cid":...}（常驻页面池，LRU）
        self._send_locks = {}  # cid -> asyncio.Lock（同群发送串行化，防并发打字串行错乱）
        self._token_dirty = False  # token 变化 → 下次用到某页时按需 reload（不再无条件重载主群页）
        self._fail_streak = 0  # 浏览器连续失败计数（连续失败触发自动重启）
        self._consecutive_send_fail = 0  # 连续发送失败计数（含兜底失败，自愈无效告警用）
        self._send_alerted = False  # 发送失败告警防重（恢复后重置）
        self._last_token_value = ""  # 上次记录的 token 值（检测变化触发页面 reload）
        self._last_reload = 0.0      # 上次页面 reload 时间（兜底防会话老化）

    async def send_with_id(self, text: str, cid: str) -> str:
        """需要绑定确认提示时，使用当前账号连接的成功回执。"""
        if _LIVE_SIO is None:
            raise RuntimeError("账号连接不可用")
        return await _LIVE_SIO.send_text_confirmed(cid, NUO_UID, text)

    async def send_reply_with_id(self, text: str, cid: str, reference: dict, *, guard=None) -> str:
        from runtime.reply_delivery import ReplyNotSent
        if _LIVE_SIO is None:
            raise ReplyNotSent("账号连接不可用")
        lock = self._send_locks.setdefault(cid, asyncio.Lock())
        async with lock:
            mid = await _LIVE_SIO.send_reply_confirmed(cid, NUO_UID, text, reference, guard=guard)
            if not mid:
                return ""
            # ACK 只证明平台接受消息；还需确认平台保留了原生引用，没有静默忽略。
            for attempt in range(3):
                rows = await asyncio.to_thread(get_messages, cid, 30)
                for row in rows or ():
                    if row.get("message_id") != mid:
                        continue
                    content = row.get("content") or {}
                    actual_ref = content.get("reference") or {}
                    if (row.get("sent_by") == NUO_UID and content.get("text") == text
                            and actual_ref.get("id") == reference.get("id")):
                        return mid
                    raise RuntimeError("引用回复读回不一致，不自动重发")
                if attempt < 2:
                    await asyncio.sleep(0.4 * (attempt + 1))
            raise RuntimeError("引用回复未能读回，不自动重发")

    async def send_character_with_id(self, resource_id: str, cid: str) -> str:
        """分享卡走当前账号连接，禁止当文本发送或模糊失败后换通道。"""
        if _LIVE_SIO is None:
            raise RuntimeError("账号连接不可用")
        return await _LIVE_SIO.send_character_confirmed(cid, NUO_UID, resource_id)

    async def start(self):
        from playwright.async_api import async_playwright
        cookies = token_store.get_cookies_for_playwright()
        self._last_token_value = next((c.get("value", "") for c in cookies
                                       if c.get("name") == "sb-rls-auth-token"), "")
        self._pw = await async_playwright().start()
        proxy_url = env_config.get_proxy_url()
        self.ctx = await self._pw.chromium.launch_persistent_context(
            str(ROOT / self.profile_dir), headless=True, channel=env_config.get_channel(),
            viewport={"width": 1280, "height": 800},
            proxy={"server": proxy_url} if proxy_url else None,
            # 消 headless 指纹（2026-08-22 实测）：去掉 navigator.webdriver + 伪装真实 Chrome UA
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu",
                  "--disk-cache-size=52428800"])
        await self.ctx.add_cookies(cookies)
        # 预加载常驻群（每个群一张常驻页），之后发送不再 goto
        await self.navigate(self.primary_cid)
        log("sender ready（常驻页面 " + str(len(self.pages)) + " 张）")

    async def _page_for(self, cid: str):
        """取该群的常驻页面（懒创建 + LRU 淘汰）；私聊共用一个 scratch 页。返回页面条目."""
        key = cid if cid in self._known_groups else "__scratch__"
        ent = self.pages.get(key)
        if ent is not None and ent["page"].is_closed():
            self.pages.pop(key, None)
            ent = None
        if ent is None:
            if len(self.pages) >= PAGE_CACHE_MAX:
                # 淘汰最久未用的页；主群页永不淘汰（它最忙，被淘汰后每次问候/排行都要重载）
                cands = [(k, v) for k, v in self.pages.items() if k != self.primary_cid]
                if not cands:
                    cands = list(self.pages.items())
                oldest = min(cands, key=lambda kv: kv[1]["ts"])
                try:
                    await oldest[1]["page"].close()
                except Exception:
                    pass
                self.pages.pop(oldest[0], None)
            ent = {"page": await self.ctx.new_page(), "ts": time.time(),
                   "loaded": False, "cid": None}
            self.pages[key] = ent
        ent["ts"] = time.time()
        self.page = ent["page"]
        return ent

    async def _load_group(self, page, cid: str) -> bool:
        """把某个页面加载到指定群的聊天页并等输入框就绪."""
        try:
            await page.goto(ORIGIN + "/chat?c=" + cid, wait_until="commit", timeout=30000)
        except Exception as e:
            log("  goto 失败: " + str(e)[:50])
            return False
        for _ in range(80):  # 80 × 0.25s = 20s
            await self._dismiss_overlay(page)
            try:
                ta = await page.query_selector('textarea[placeholder*="输入消息"]')
                if ta and await ta.is_visible():
                    await asyncio.sleep(0.4)  # 预热：等 React 受控组件就绪
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        return False

    async def navigate(self, cid: str) -> bool:
        """切到指定群的页面（常驻页直接复用，只有新建/掉线/需重载时才 goto）."""
        ent = await self._page_for(cid)
        page = ent["page"]
        self.current_cid = cid
        dirty = self._token_dirty or (time.time() - self._last_reload > PAGE_MAX_AGE_SEC)
        if ent["loaded"] and ent["cid"] == cid and not dirty:
            try:
                await self._dismiss_overlay(page)
                ta = await page.query_selector('textarea[placeholder*="输入消息"]')
                if ta and await ta.is_visible():
                    return True
            except Exception:
                pass
        ok = await self._load_group(page, cid)
        ent["loaded"], ent["cid"] = ok, cid
        if ok:
            self._token_dirty = False
            self._last_reload = time.time()
        return ok

    async def _goto_primary(self) -> None:
        """切到常驻群（兼容旧调用；现在是复用常驻页面，不再 goto）."""
        await self.navigate(self.primary_cid)

    async def _dismiss_overlay(self, page=None) -> None:
        """关闭可能遮挡输入框的公告/Modal 弹窗。无遮罩时零副作用、零按钮查询。"""
        page = page or self.page
        if page is None:
            return
        try:
            # 先检测是否有全屏遮罩（公告弹窗特征：fixed + inset-0 覆盖全屏 + 可见）。
            # 无遮罩直接返回——避免误点聊天页其他 [aria-label="Close"] 按钮（图片预览/面板等）。
            has_overlay = await page.evaluate(
                """() => {
                    for (const el of document.querySelectorAll('div.fixed.inset-0')) {
                        const st = getComputedStyle(el);
                        if (st.display !== 'none' && st.visibility !== 'hidden') return true;
                    }
                    return false;
                }"""
            )
            if not has_overlay:
                return
            # 有遮罩才找关闭按钮：优先「知道了」类确认按钮，× 图标兜底
            for sel in ('button:has-text("知道了")', 'button:has-text("我知道了")',
                        '[aria-label="Close"]'):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=3000)
                        log("  dismiss overlay: 关闭弹窗 [" + sel + "]")
                        await asyncio.sleep(0.5)
                        return
                except Exception:
                    continue
        except Exception:
            pass

    async def _harvest_token(self) -> bool:
        """P12-BK：把**浏览器里的** `sb-rls-auth-token` 抄回 token JSON。

        ## 为什么需要（单向刷新的缺口）

        `_refresh_token()` 只做 **JSON → 浏览器**（把续期后的新 cookie 推进页面）。
        但平台的 SPA **自己也会刷 token**，刷完把**新的** refresh_token 留在浏览器里
        （refresh_token 是**轮换制**：旧的随即作废）。我们 JSON 里那份就成了旧的
        ⇒ `renew_token` 拿它去续期，平台回"未授权" ⇒ 误判"会话真死"。

        这正是"续期失效几小时后自己又好"最可能的机械原因（见
        `docs/P12-BI-风控墙与会话真死分开.md`）：页面刷新过一次，我们手里那份才跟上。

        ## 规则

        * 只往"更新"的方向抄：解出两边 payload 的 `expires_at`，浏览器那份**更大**才写回；
        * 60 秒节流（`ctx.cookies()` 是本地操作，但也别每条消息都读）；
        * 任何异常都不影响发送（返回 False 即可）。
        """
        if not self.ctx:
            _session_obs.harvest("no_context")
            return False
        _now = time.time()
        if _now - getattr(self, "_last_harvest", 0) < TOKEN_HARVEST_SEC:
            _session_obs.harvest("throttled")
            return False
        self._last_harvest = _now
        # P12-BO / S2：**写凭据必须和续期/重登抢同一把锁**。
        # 凭据文件是**单文件 bind mount** ⇒ `os.replace` 会 EBUSY ⇒ `token_store`
        # 只能"就地截断写"（非原子）。此刻若 `renew_token`/`headless_relogin` 也在写，
        # 两个进程交错就会把 JSON 写花。`session_guard` 全项目共用一把 `.session.lock`，
        # 所以这里套上它就够了；`timeout=0` = 拿不到就跳过这一轮
        #（抄回本来就是机会主义行为，下一条消息还有机会）。
        try:
            from session_guard import guard as _guard
            with _guard("token_harvest", timeout=0) as _got:
                if not _got:
                    _session_obs.harvest("lock_busy")
                    return False
                return await self._harvest_token_locked()
        except Exception as e:                                 # noqa: BLE001
            _session_obs.harvest("error", error_type=type(e).__name__)
            log("  token 抄回失败（不影响发送）: " + str(e)[:60])
            return False

    async def _harvest_token_locked(self) -> bool:
        """`_harvest_token()` 的实体（已持有会话锁）。"""
        try:
            import base64 as _b64
            ck = await self.ctx.cookies()
            brow = next((c.get("value", "") for c in ck
                         if c.get("name") == "sb-rls-auth-token"), "")
            if not brow:
                _session_obs.harvest("missing_browser_cookie", browser=ck, origin=ORIGIN)
                return False
            stored_cookie = token_store.get_cookie("sb-rls-auth-token") or {}
            cur = stored_cookie.get("value", "")
            if not cur or brow == cur:
                _session_obs.harvest("same" if cur else "missing_file_cookie",
                                     browser=ck, stored=[stored_cookie], origin=ORIGIN)
                return False

            def _exp(v: str) -> float:
                try:
                    p = v[len("base64-"):] if v.startswith("base64-") else v
                    p += "=" * (-len(p) % 4)
                    return float(json.loads(_b64.b64decode(p)).get("expires_at") or 0)
                except Exception:                              # noqa: BLE001
                    return 0.0

            if _exp(brow) <= _exp(cur):
                _session_obs.harvest("expiry_not_later", browser=ck,
                                     stored=[stored_cookie], origin=ORIGIN)
                return False
            saved = token_store.update_cookie("sb-rls-auth-token", brow)
            browser_cookie = next(cookie for cookie in ck if cookie.get("name") == "sb-rls-auth-token")
            _session_obs.harvest_written(saved, browser_cookie,
                lambda: token_store.get_cookie("sb-rls-auth-token"), ORIGIN)
            log("🔑 浏览器里的 token 比 JSON 新 → 已抄回（SPA 自己刷新过）")
            return True
        except Exception as e:                                 # noqa: BLE001
            _session_obs.harvest("error", error_type=type(e).__name__)
            log("  token 抄回失败（不影响发送）: " + str(e)[:60])
            return False

    async def _refresh_token(self) -> bool:
        """发消息前刷新 cookie（从 JSON 读最新值覆盖，防 Playwright 旧 cookie 过期）。

        为什么无条件：renew_token 每 5 分钟续期，JSON 里 token 永远新鲜（剩余 >600s），
        但 Playwright context 里的 cookie 只在 add_cookies 时更新。若长时间不发消息，
        Playwright 旧 cookie 已过期，用「剩余时间」判断会误判为新鲜而不刷新。
        add_cookies 是本地操作（<50ms），无条件刷新代价可忽略。

        token 变化检测：DZMM 前端页面加载时把 cookie 会话读入内存，renew 续期更新 cookie
        后页面内存会话仍是旧的 → 发送「假成功」（Playwright 操作成功但消息未投递）。
        因此检测到 token 值变化（或页面存活超 2 小时兜底）时 reload 页面，让 JS 重读新 cookie。
        返回是否成功。
        """
        observation = _session_obs.begin_sync()
        try:
            if not self.ctx:
                _session_obs.event("cookie_sync_skipped", result="no_context", important=False)
                return True  # dry 模式无浏览器上下文，跳过
            await self._harvest_token()      # P12-BK：先看浏览器那边有没有更新（内部节流）
            cookies = token_store.get_cookies_for_playwright()
            new_token = next((c.get("value", "") for c in cookies
                              if c.get("name") == "sb-rls-auth-token"), "")
            token_changed = bool(new_token) and new_token != self._last_token_value
            page_old = time.time() - self._last_reload > PAGE_MAX_AGE_SEC
            token_expired = self._access_expired()
            before = await _session_obs.sync_before(self.ctx, cookies, ORIGIN)
            await self.ctx.add_cookies(cookies)
            # token 变了才标记「需重载」（防假成功）；改成惰性：下次用到哪张页面就只重载那一张，
            # 不再无条件重载主群页（多群常驻后，无条件重载会白等一次整页导航）。
            # access_token 过期时跳过 page_old 强制重载（撞验证码+过期时重载会重新认证失败，
            # 保持旧会话反而能继续收发）
            if token_changed or (page_old and not token_expired):
                self._token_dirty = True
                self._last_token_value = new_token
            await _session_obs.sync_after(self.ctx, before, ORIGIN)
            return True
        except Exception as e:
            _session_obs.event("cookie_sync_failed", error_type=type(e).__name__)
            log("  token 刷新失败: " + str(e)[:60])
            return False
        finally:
            _session_obs.end_sync(observation)

    def _access_expired(self) -> bool:
        """读 token JSON 判断 access_token 是否已过期（本地判断，不打网络）。"""
        try:
            import base64
            c = token_store.get_cookie("sb-rls-auth-token")
            if not c:
                return True
            v = c.get("value", "")
            payload = v[len("base64-"):] if v.startswith("base64-") else v
            payload += "=" * (-len(payload) % 4)
            d = json.loads(base64.b64decode(payload))
            return d.get("expires_at", 0) - time.time() < 0
        except Exception:
            return True

    def _record_fail(self) -> None:
        """发送失败计数：撞验证码时不累积（navigate 失败是风控拦截，重启也救不回来，只空转）。"""
        if not _ctx.captcha_active():
            self._fail_streak = self._fail_streak + 1

    async def reload_page(self) -> None:
        """重新加载当前页面（让页面 JS 重新从 cookie 读会话，防发送假成功）."""
        try:
            log("  ♻️ token 已更新，reload 页面刷新会话...")
            cid = self.current_cid or self.primary_cid
            ent = await self._page_for(cid)
            ent["loaded"] = await self._load_group(ent["page"], cid)
            ent["cid"] = cid
            if ent["loaded"]:
                self._token_dirty = False
                self._last_reload = time.time()
            log("  ♻️ reload 完成（已预热）")
        except Exception as e:
            log("  ♻️ reload 失败: " + str(e)[:60])
            self._last_reload = time.time()

    async def send(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        """按群串行化发送（后台任务和主循环可能同时往同一个群发，共用一张页面，
        并发打字会串行错乱）."""
        from runtime import lambda_bot
        control = lambda_bot.is_control_send()
        created_at = time.time()
        text = lambda_bot.render_text(text, cid)
        lock = self._send_locks.get(cid)
        if lock is None:
            lock = self._send_locks[cid] = asyncio.Lock()
        async with lock:
            if not control and lambda_bot.suppress_outbound(cid, created_at):
                return False
            return await self._send_locked(text, cid, allow_fallback)

    async def _send_locked(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        """发送消息；navigate 间歇失败/浏览器卡顿自动重试一次.
        allow_fallback=False 时纯 Playwright 发送，不走 Socket.IO 兜底（私聊发骰用，避免假成功）。
        长消息（> `ACCOUNT_MSG_MAX_CHARS`）先试 Socket.IO 一条发（P12-AZ）。"""
        # 热更新 token：发消息前确保 cookie 新鲜（renew_token 续期后零中断生效）
        if not await self._refresh_token():
            log("⚠️ token 热更新失败，主动退出交给 entrypoint 重启")
            sys.exit(1)
        # P12-AZ：长消息先试 Socket.IO —— 它不受打字框 200 字限制，能**一条**发完
        # （认证群走 Bot API 也是一条，观感就一致了）。它没成再走下面的"切 ≤190 字 + 打字"。
        # 短消息不碰这条路（打字更稳，也不比它慢）。私聊发骰那种 `allow_fallback=False`
        # 的发送也照旧只走 Playwright。
        if allow_fallback and len(text) > ACCOUNT_MSG_MAX_CHARS:
            if await self._sio_send_long(text, cid):
                self._fail_streak = 0
                self._consecutive_send_fail = 0
                self._send_alerted = False
                return True
        for attempt in range(2):
            if attempt > 0:
                await asyncio.sleep(2)
            _t0 = time.time()
            try:
                if not await self.navigate(cid):
                    log("  send fail: navigate 失败 cid=" + cid[:8])
                    self._record_fail()
                    continue
                _t1 = time.time()
                ta = await self.page.query_selector('textarea[placeholder*="输入消息"]')
                if not ta:
                    log("  send fail: 找不到 textarea, URL=" + self.page.url[:60])
                    self._record_fail()
                    continue
                if not await ta.is_visible():
                    log("  send fail: textarea 不可见")
                    self._record_fail()
                    continue
                await ta.click(timeout=5000)
                # 等 React 受控组件响应 click（focus + 事件绑定），避免 insert_text 空输入
                await asyncio.sleep(0.3)
                # P12-AN：**账号通道单条上限 200 字**（实测 190 字能发、210 字发不出，
                # 2026-09-29 用 `bot测试` 群夹逼出来的），超限时平台**静默丢弃** ——
                # 我们的代码只看到"假成功检测"，而出站队列还把它记成 sent。
                # 后果：顶层茶水间的缺勤回顾（400+ 字）一条都发不出去（屋主："@你怎么没反应了"）。
                # 对策：超过上限就**切成多条**依次发（每条都照常读回验证）。
                parts = _split_for_account(text, ACCOUNT_MSG_MAX_CHARS)
                if len(parts) > 1:
                    log("  ✂️ 长消息切成 {} 条（每条 ≤{} 字）".format(
                        len(parts), ACCOUNT_MSG_MAX_CHARS))
                _t2 = time.time()
                for _pi, _part in enumerate(parts):
                    if _pi:
                        # 上一段发完再打下一段：重新点一下输入框（焦点可能丢）
                        try:
                            await ta.click(timeout=5000)
                            await asyncio.sleep(0.2)
                        except Exception:                          # noqa: BLE001
                            pass
                    _plines = [ln for ln in _part.split("\n")]
                    # P12-K：玫瑰房星象卡改五段式后有 18~20 行，旧上限 10 会把卡片砍半
                    # （`personas.ROSE_SIGN_MAX_LINES`）。这条链路只在官方 Bot API
                    # 明确拒绝时兜底用，多贴几行没有副作用。
                    if len(_plines) > _personas.ROSE_SIGN_MAX_LINES:
                        _plines = _plines[:_personas.ROSE_SIGN_MAX_LINES]
                    # 逐行输入：行间用 Shift+Enter 换行（不发送），最后 Enter 发送。
                    # insert_text 一次性插入（已实测 React 正确接收，比逐字符 type 快 ~500 倍）。
                    for i, line in enumerate(_plines):
                        await self.page.keyboard.insert_text(line)
                        if i < len(_plines) - 1:
                            await self.page.keyboard.press("Shift+Enter")
                    await self.page.keyboard.press("Enter")
                    await asyncio.sleep(0.3 if _pi == len(parts) - 1 else 1.0)
                    _t2 = time.time()
                    if not await self._verify_sent(cid, _part):
                        if len(parts) > 1:
                            log("  ⚠️ 第 {}/{} 段未投递".format(_pi + 1, len(parts)))
                        self._record_fail()
                        break
                else:
                    # 全部段落都读回成功才算成功
                    self._fail_streak = 0
                    self._consecutive_send_fail = 0
                    self._send_alerted = False
                    _tot = time.time() - _t0
                    if _tot >= 3.0:  # 慢发送留痕，便于定位卡顿
                        log("  ⏱️ send 耗时 " + str(round(_tot, 1)) + "s"
                            + "（切页 " + str(round(_t1 - _t0, 1))
                            + "s / 输入 " + str(round(_t2 - _t1, 1))
                            + "s / 读回 " + str(round(_tot - (_t2 - _t0), 1)) + "s）")
                    return True
                log("  ⚠️ 假成功检测：消息未投递到群里，重试")
                self._record_fail()
                continue
            except Exception as e:
                log("  pw: " + str(e)[:60])
                self._record_fail()
                continue
        # Playwright 两次都失败，降级用 DZMMSocket（HTTP 快速发送）兜底
        if allow_fallback:
            ok = await self._fallback_send(text, cid)
        else:
            ok = False  # 纯 Playwright，不用 Socket.IO 兜底（避免假成功）
        # 浏览器连续失败达阈值，自动重启自愈（防止页面卡死/进程崩溃长期无响应）
        if self._fail_streak >= 3 and not _ctx.captcha_active():
            await self._restart()
        # 发送失败计数 + 持续失败告警（自愈/兜底都救不回才报，避免偶发抖动刷屏）
        if ok:
            self._consecutive_send_fail = 0
            self._send_alerted = False
        else:
            self._consecutive_send_fail += 1
            if self._consecutive_send_fail >= 3 and not self._send_alerted:
                self._send_alerted = True
                if _feat_on("", "alert_send_fail"):
                    try:
                        await asyncio.to_thread(send_weixin,
                            "🚨【小小糯bot】发送持续失败（连续 3 条，自愈无效），请检查代理/cookie")
                    except Exception:
                        pass
        return ok

    async def _verify_sent(self, cid: str, text: str) -> bool:
        """读回验证：确认消息真的投递到群里（防 Playwright 假成功）。
        返回 True=已投递（或读回失败保守放行）；False=确认未投递。
        注意：这里的等待不能压太短——太短会读不到刚发的消息 → 误判假成功 → 重发 → 群内重复。
        （2026-09-14 曾试过压到 0.7s，风险大于收益，退回 1.2s）"""
        await asyncio.sleep(1.2)
        try:
            msgs = await asyncio.to_thread(get_messages, cid, 5)
            probe = (text.split("\n")[0] or text)[:20].strip()
            for m in msgs:
                if not is_self(m.get("sent_by")):
                    continue
                mt = (m.get("content") or {}).get("text", "")
                if probe and probe in mt:
                    return True
            return False  # 最近 5 条里没有 bot 刚发的 → 假成功
        except Exception:
            return True  # 读回失败（网络抖动）保守放行，避免误判重发

    async def _fallback_send(self, text: str, cid: str) -> bool:
        """Playwright 发送失败后的兜底：DZMMSocket HTTP 快速发送（静默降级）。"""
        try:
            import importlib.util
            # 用独立模块名加载项目自己的 socketio.py，避免污染 sys.modules['socketio']
            # （sio_subscribe import 后会从 sys.path 移除项目目录，且 socketio 已被 python-socketio 占用）
            spec = importlib.util.spec_from_file_location("dzmm_socketio", str(ROOT / "socketio.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d = mod.DZMMSocket(ORIGIN)
            ok = await asyncio.to_thread(d.send_message, cid, NUO_UID, text, get_cached_cookie())
            # P12-AN：Socket.IO 那条路只看 HTTP 状态码，**不读回** —— 它是"假成功"的来源：
            # 2026-09-29 现场出站队列里躺着 4 条 status=sent 的回顾，群里一条都没有。
            # 所以这里补一次读回验证（跟 Playwright 那条路同一个判据）。
            if ok:
                await asyncio.sleep(1.2)
                if not await self._verify_sent(cid, text):
                    log("  fallback(DZMMSocket): ⚠️ 读回没看到，判为未投递")
                    ok = False
            log("  fallback(DZMMSocket): " + ("✅" if ok else "❌") + " cid=" + cid[:8])
            return ok
        except Exception as e:
            log("  fallback err: " + str(e)[:60])
            return False

    async def _sio_send_long(self, text: str, cid: str) -> bool:
        """长消息走 Socket.IO **一条**发（P12-AZ）。唯一成功判据是**读回**。

        为什么每轮都读回、而不看回执：这条路失败时 `connect` 会返回
        `400 Session ID unknown`，可 **`send` 有时照样返回 `200 ok` 而群里什么都没有**
        （P12-AN 那条"假成功"就是它）。读回顺带防重复：上一轮其实发出去了，这一轮就不会再发。

        它没成也不报错、不返回失败 —— 调用方接着走"切 ≤190 字 + 打字"那条老路，
        所以最坏情况只是退回到 P12-AZ 之前的观感（两条），不会变成没有回复。
        """
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "dzmm_socketio", str(ROOT / "socketio.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:                                  # noqa: BLE001
            log("  📡 socket 长消息不可用: " + str(e)[:60])
            return False
        try:
            sock = mod.DZMMSocket(ORIGIN)
            cookie = get_cached_cookie()
        except Exception as e:                                  # noqa: BLE001
            log("  📡 socket 长消息初始化失败: " + str(e)[:60])
            return False
        t0 = time.time()
        note = "?"
        # ① 先试**已经连着的那条订阅长连接**（读消息用的那条，不用握手 ⇒ 不会撞
        #    `Session ID unknown`）。它是 fire-and-forget，所以照样只能看读回。
        sub = _LIVE_SIO
        if sub is not None and getattr(sub, "is_connected", False):
            try:
                mid_live = await sub.send_text(cid, NUO_UID, text)
            except Exception as e:                              # noqa: BLE001
                mid_live = ""
                log("  📡 长连接发送异常: " + str(e)[:60])
            if mid_live:
                await asyncio.sleep(2.0)
                if await self._verify_landed(cid, text, mid_live):
                    log("  📡 长消息**一条**发完（{} 字 / 长连接 / {:.1f}s）".format(
                        len(text), time.time() - t0))
                    return True
                log("  📡 长连接没落地，改用 HTTP 握手重试")
        # ② 再试 HTTP 握手那条（时好时坏，但独立于订阅连接）
        for i in range(1, SIO_LONG_MAX_TRIES + 1):
            try:
                _ok, note, mid = await asyncio.to_thread(
                    sock.send_message_ex, cid, NUO_UID, text, cookie, 1)
            except Exception as e:                              # noqa: BLE001
                note, mid = "err=" + str(e)[:40], ""
            await asyncio.sleep(2.0)
            if await self._verify_landed(cid, text, mid):
                log("  📡 长消息**一条**发完（{} 字 / 第 {} 次 / {:.1f}s）".format(
                    len(text), i, time.time() - t0))
                return True
            log("  📡 第 {} 次没落地（{}），重新握手".format(i, note or "ok"))
            if time.time() - t0 >= SIO_LONG_BUDGET_SEC:
                break
        log("  📡 长消息 Socket.IO 没成（{}），退回切分打字".format(note or "?"))
        return False

    async def _verify_landed(self, cid: str, text: str, message_id: str = "") -> bool:
        """读回验证（P12-AZ）：要的是**新鲜**的那一条。

        与 `_verify_sent` 的差别：
          · 窗口更大（`SIO_LONG_READBACK`=20 条）—— 茶水间 792 人，5 条窗口转眼就滚出去了；
          · **按时间卡新鲜度** —— 冷却期里重发同一张运势卡时，窗口里正好躺着上一次那张，
            文本逐字相同（P12-AW 的冷却复用），不卡时间就会把"没发出去"判成"发出去了"，
            然后就**静默丢一条**。宁可多读一次，也别把没发生的当成发生了。
          · 读回接口连续失败时**返回 False**（调用方会退回切分打字）——
            宁可偶尔重复一条，也别像 P12-AN 那样整条消失。
          · 时间戳读不出来（空）也算没落地 —— 同样的取舍：**宁可重复，不可静默丢**。
        时间戳是 UTC（平台 `sent_at` 存的是 UTC，实测茶水间 09:43 发的存 `01:43`）。
        """
        probe = (text.split("\n")[0] or text)[:20].strip()
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 10))
        for attempt in range(3):
            try:
                msgs = await asyncio.to_thread(get_messages, cid, SIO_LONG_READBACK)
            except Exception as e:                              # noqa: BLE001
                log("  📡 读回失败（第 {} 次）：{}".format(attempt + 1, str(e)[:40]))
                await asyncio.sleep(1.5)
                continue
            for m in msgs or []:
                if not is_self(m.get("sent_by")):
                    continue
                if message_id and m.get("message_id") == message_id:
                    return True
                mt = (m.get("content") or {}).get("text", "")
                if not probe or probe not in mt:
                    continue
                stamp = (m.get("sent_at") or "")[:19]
                if stamp and stamp >= cutoff:
                    return True
            return False
        return False

    async def _restart(self):
        """浏览器连续失败，自动重启自愈（close 后重新 start）。"""
        log("  ⚠️ 浏览器连续失败 " + str(self._fail_streak) + " 次，自动重启自愈...")
        try:
            await self.close()
        except Exception:
            pass
        self._fail_streak = 0
        try:
            await self.start()
            log("  ✅ 浏览器自动重启完成")
        except Exception as e:
            log("  ❌ 浏览器自动重启失败: " + str(e)[:80])

    async def close(self):
        try:
            if self.ctx:
                await self.ctx.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self.ctx = None
        self.page = None
        self.pages = {}
        self.current_cid = None
        self._pw = None
