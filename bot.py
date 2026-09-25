#!/usr/bin/env python3
"""DZMM Agent — c5269e31 专用。全异步 + Playwright 常驻复用，低延迟."""
# build-marker: dsh-link-check-2026-08-31  # dsh 部署链路验证标记（无害，可删）
import asyncio, json, os, sys, time, random, hashlib, re, subprocess, unicodedata
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HERMES_PY = sys.executable  # auto_recover 重启 bot 用（当前解释器，容器/裸机通用）
PAGE_CACHE_MAX = 3  # 常驻页面池上限（LRU 淘汰）；1.8G 内存的机器上别开太多 chromium 页
GROUPS = [
    "bd7fe6a4-0065-4551-ad3d-94116dba8f9c",  # 认证妹子群（主群，常驻）
    "93cc6c7b-ba9e-48f3-a106-3074483aebbd",  # 裳姐姐和她的骚贱小母狗
    "38698306-4252-438a-aa56-7dbab89250eb",  # 被dzmm团本折磨的三人
    "007bfd75-2efb-491c-8233-20046af69b24",  # 机电驱动与智能算法研讨群
    "4a56be71-9ca6-4785-b45f-18725d187f1a",  # Rose（505）
]

# 静默监控群：只读不回（不回复、不接指令、不筛查、不发欢迎语、不推送、不同步档案）。
# 想恢复功能，把 cid 从这张表里删掉即可。
MUTE_CIDS = set()

# 房间群（独立人设 + 屋主权限）：配置见 personas.ROOMS
from personas import (ROOMS, apply_persona, set_cid as set_persona_cid,
                      ROOM_GREET_TEMPLATE, ROOM_GREET_ITEMS, ROOM_GREET_LINES,
                      ROOM_GUEST_REFUSE, ROOM_ORDER_VERBS, ROOM_MENU_WORDS,
                      ROOM_LEWD_WORDS, ROOM_FLOWER_WORDS, ROOM_FLOWER_NOUNS,
                      ROOM_FLOWER_VERBS, ROOM_GHOST_TEXT, ROOM_GHOST_WARN,
                      ROOM_GHOST_BACK, ROOM_GHOST_WARN_COOLDOWN,
                      ROOM_GHOST_MAX_H, ROOM_GHOST_COOLDOWN, ROSE_CID, VV_UID, NUONUO_UID,
                      ROSE_SIGNS, ROSE_SIGN_WORDS,
                      ROSE_LUCKY_ITEMS, ROSE_LUCKY_DIRS, ROSE_LUCKY_COLORS,
                      ROSE_SIGN_YI, ROSE_SIGN_JI, ROSE_MOONS, p)
ROOM_CIDS = set(ROOMS.keys())

# 入群筛查生效的群（踢游客 / 踢男性 / 提醒未认证 + 「妹子认证群」文案）。
# 不在这张表里的群：新人入群只发中性欢迎语，不踢人、不发认证群文案。
SCREEN_CIDS = {
    "bd7fe6a4-0065-4551-ad3d-94116dba8f9c",  # 认证妹子群
    "93cc6c7b-ba9e-48f3-a106-3074483aebbd",  # 裳姐姐和她的骚贱小母狗
    "38698306-4252-438a-aa56-7dbab89250eb",  # 被dzmm团本折磨的三人
}
# 定时推送生效的群（早/中/晚问候、12·18 点骚度排行、每日记忆汇报）
PUSH_CIDS = [
    "bd7fe6a4-0065-4551-ad3d-94116dba8f9c",  # 认证妹子群
    "007bfd75-2efb-491c-8233-20046af69b24",  # 机电驱动与智能算法研讨群
]
_ACTIVE_CID = GROUPS[0]  # 当前处理的群，主循环每次迭代设置
PRIMARY_CID = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"  # 常驻群（妹子认证群，page 常驻不 goto）
NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"  # 侧车账号（登录态持有者）

# ---------------------------------------------------------------- 自身身份（P4.2）
# 从 P4 起，「我自己」有**两个**身份，必须分清：
#   NUO_UID  —— 侧车账号：私聊 / 图片 / 分享 / 其他群，以及历史数据里的旧发言
#   BOT_UID  —— 官方 Bot：群文本发送（P4.1 的 RoutingSender）
# 回声过滤与「排除自己」必须同时认这两个。否则 Bot 的发言会被当成群友 ——
# 轻则统计污染，重则自触发循环。
BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()
BOT_NAME = (os.environ.get("DZMM_BOT_NAME") or "小小糯").strip()
BOT_NAME_SHORT = (os.environ.get("DZMM_BOT_SHORT") or "小小糯").strip()


def self_uids() -> set:
    """全部「我自己」的 uid。"""
    ids = {NUO_UID}
    if BOT_UID:
        ids.add(BOT_UID)
    return ids


def is_self(uid) -> bool:
    """这条消息是不是我自己发的（侧车账号或官方 Bot）。"""
    if not uid:
        return False
    return uid == NUO_UID or (bool(BOT_UID) and uid == BOT_UID)


def self_names() -> set:
    """判定「@的是我」用的名字集合。

    账号名查成员表；Bot 名**读配置**（Bot API 没有成员接口，查不到）。
    """
    names = {get_full_name(NUO_UID), get_name(NUO_UID)}
    if BOT_UID:
        names.add(BOT_NAME)
        names.add(BOT_NAME_SHORT)
    return {n for n in names if n}

OWNER_UID = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"  # 糯糯～（群主，可代注册）

# 涩涩骰子词库
SCENES = ["更衣室", "办公室", "教室", "浴室", "天台", "停车场", "公厕隔间", "深夜公园", "KTV包间", "楼梯间"]
POSITIONS = ["后入", "骑乘", "69", "口爆", "颜射", "捆绑", "spanking", "道具（跳蛋·振动棒·肛塞）", "项圈牵引", "露出", "强制高潮", "失禁play", "兽耳cos", "师生角色扮演", "主人与宠物", "双飞", "群P"]
EXTRAS = ["蒙眼", "手铐", "戴项圈", "不许出声", "公开play被围观", "边被玩边打电话", "塞着跳蛋出门", "让她求饶才停"]

# XP → 场景/体位/附加 偏好（发骚骰子按目标 XP 定制）
XP_BIAS = {
    "小M": {"pos": ["后入", "口爆", "捆绑", "项圈牵引", "spanking", "失禁play"]},
    "被支配": {"pos": ["后入", "捆绑", "强制高潮", "项圈牵引"]},
    "小S": {"pos": ["骑乘", "颜射", "spanking", "强制高潮"]},
    "支配": {"pos": ["骑乘", "spanking", "强制高潮"]},
    "露出": {"scene": ["天台", "深夜公园", "停车场", "公厕隔间"], "extra": ["露出", "公开play被围观", "塞着跳蛋出门"]},
    "口交": {"pos": ["口爆", "69"]},
    "绳缚": {"pos": ["捆绑"], "extra": ["手铐", "戴项圈"]},
    "母狗化": {"extra": ["戴项圈", "让她求饶才停"]},
}

from dzmm import get_messages, get_messages_all, get_messages_since, get_user, get_chatroom_name, get_members, remove_member, get_verified, get_private_cid
from dice_game import roll_dice, roll_wild, dice_str, count_point, is_legal_raise, judge_open, parse_call
from brains import generate_reply, generate_summary, generate_profile_summary, generate_person_summary, generate_rank, generate_insult, generate_insult_continue, generate_poem, generate_poem_theme, generate_archive, generate_fortune, generate_greeting, generate_horoscope, generate_recap, generate_trend, generate_defend, generate_scene, generate_scene_continue, generate_scene_end, generate_scene_rewrite, generate_xp, generate_diaojiao, generate_diaojiao_end, generate_perf_enrich, generate_perf_insult, generate_perf_comment, generate_perf_ending, generate_perf_novel, generate_perf_novel_chunked, generate_heat_judge, generate_heat_climax, generate_heat_fasao, generate_story_start, generate_story_step, generate_story_end, generate_vote_options, generate_devil_roast, generate_memory_summary, POEM_STYLES, _call_llm
from profiles import get_profile, upsert_profile, load_profiles, save_profiles
from wx_alert import send_weixin
import token_store
import env_config
import state_store                                    # P7：状态存储后端（默认 SQLite）

# DZMM 主站 origin（多线路切换：env_config 从 DZMM_HOST 环境变量读）
ORIGIN = env_config.get_dzmm_origin()

# Cookie 缓存（30s 刷新一次，避免每次读 token JSON）
_cookie_cache = {"cookie_str": "", "ts": 0.0}

# 撞验证码状态（进程内全局）：撞验证码时置 True，HTTP 恢复后置 False
_captcha_active = False
_captcha_notified = {}  # cid -> bool，撞验证码群里说明是否已发（每群一次防刷屏）

# 撞验证码相关文案
CAPTCHA_NOTICE = ("⚠️ 小小糯撞上平台验证码啦～查资料、踢人、新人筛查暂时受限，"
                  "聊天收发不受影响哦～风控解除会自动恢复，稍安勿躁～♡")
CAPTCHA_JOIN_NOTICE = ("⚠️ 检测到新成员「{name}」加入，但当前撞平台验证码、暂时核实不了身份～"
                       "恢复后男生和游客会被自动踢出，其他情况会提醒管理员处理哦～♡")
CAPTCHA_RECOVERED_NOTICE = ("✅ 验证码风控解除啦～查资料、踢人、新人筛查都恢复正常了哦～"
                            "之前延迟的新成员会陆续补筛查，稍等片刻～♡")

# 页面会话刷新：页面存活超 2 小时强制 reload（兜底防会话老化；token 变化也会触发 reload）
PAGE_MAX_AGE_SEC = 7200

# 昵称缓存（uid -> 昵称），跨轮次复用
_name_cache = {}
_group_name_cache = {}  # cid -> {"name":..., "ts":...}


def log(msg: str) -> None:
    line = datetime.now().strftime("%H:%M:%S") + " " + msg
    print(line, flush=True)
    try:
        with open(ROOT / "bot.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# 状态存储：P7 起默认走 SQLite（`state_store`），`DZMM_STORE=json` 可回退。
# 函数名与签名保持不变 —— 下面约 45 个调用点一处都不用改。
# 旧实现的问题（已核实）：写是**非原子**的 write_text（文件 bind mount 上 os.replace
# 会 EBUSY），写到一半被 kill 会留下截断 JSON，而解析失败时**静默返回 {}**，
# 导致全群发情值/高潮/状态机/记忆一起无声清零。
def load_state() -> dict:
    """读全部群状态 `{cid: {...}}`。"""
    return state_store.load_state()


def save_state(s: dict) -> None:
    state_store.save_state(s)


def get_cached_cookie() -> str:
    """缓存 cookie 字符串，30s 刷新一次."""
    if time.time() - _cookie_cache["ts"] < 30 and _cookie_cache["cookie_str"]:
        return _cookie_cache["cookie_str"]
    _cookie_cache["cookie_str"] = token_store.get_cookie_string()
    _cookie_cache["ts"] = time.time()
    return _cookie_cache["cookie_str"]


class Sender:
    """常驻 Playwright 浏览器：每个群一个常驻 page，发送时不再来回 goto。

    （2026-09-14 提速）原来每次给非主群发消息都要 goto 目标群 → 发完再 goto 回主群，
    两次整页导航约 4~6 秒，占一次回复耗时的大头。改成每群一个常驻页面后，
    发送只剩「输入 + 回车 + 读回验证」，从 8~17 秒降到 2~4 秒。
    """

    def __init__(self, profile_dir: str = "pw_profile", primary_cid: str = PRIMARY_CID):
        self.ctx = None
        self.page = None
        self.current_cid = None
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
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"])
        await self.ctx.add_cookies(cookies)
        # 预加载常驻群（每个群一张常驻页），之后发送不再 goto
        await self.navigate(self.primary_cid)
        log("sender ready（常驻页面 " + str(len(self.pages)) + " 张）")

    async def _page_for(self, cid: str):
        """取该群的常驻页面（懒创建 + LRU 淘汰）；私聊共用一个 scratch 页。返回页面条目."""
        key = cid if cid in GROUPS else "__scratch__"
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
        try:
            if not self.ctx:
                return True  # dry 模式无浏览器上下文，跳过
            cookies = token_store.get_cookies_for_playwright()
            new_token = next((c.get("value", "") for c in cookies
                              if c.get("name") == "sb-rls-auth-token"), "")
            token_changed = bool(new_token) and new_token != self._last_token_value
            page_old = time.time() - self._last_reload > PAGE_MAX_AGE_SEC
            token_expired = self._access_expired()
            await self.ctx.add_cookies(cookies)
            # token 变了才标记「需重载」（防假成功）；改成惰性：下次用到哪张页面就只重载那一张，
            # 不再无条件重载主群页（多群常驻后，无条件重载会白等一次整页导航）。
            # access_token 过期时跳过 page_old 强制重载（撞验证码+过期时重载会重新认证失败，
            # 保持旧会话反而能继续收发）
            if token_changed or (page_old and not token_expired):
                self._token_dirty = True
                self._last_token_value = new_token
            return True
        except Exception as e:
            log("  token 刷新失败: " + str(e)[:60])
            return False

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
        if not _captcha_active:
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
        lock = self._send_locks.get(cid)
        if lock is None:
            lock = self._send_locks[cid] = asyncio.Lock()
        async with lock:
            return await self._send_locked(text, cid, allow_fallback)

    async def _send_locked(self, text: str, cid: str, allow_fallback: bool = True) -> bool:
        """发送消息；navigate 间歇失败/浏览器卡顿自动重试一次.
        allow_fallback=False 时纯 Playwright 发送，不走 Socket.IO 兜底（私聊发骰用，避免假成功）。"""
        # 热更新 token：发消息前确保 cookie 新鲜（renew_token 续期后零中断生效）
        if not await self._refresh_token():
            log("⚠️ token 热更新失败，主动退出交给 entrypoint 重启")
            sys.exit(1)
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
                # 逐行输入：行间用 Shift+Enter 换行（不发送），最后 Enter 发送。
                # insert_text 一次性插入（已实测 React 正确接收，比逐字符 type 快 ~500 倍）。
                lines = [ln for ln in text.split("\n")]
                if len(lines) > 10:
                    lines = lines[:10]
                for i, line in enumerate(lines):
                    await self.page.keyboard.insert_text(line)
                    if i < len(lines) - 1:
                        await self.page.keyboard.press("Shift+Enter")
                await self.page.keyboard.press("Enter")
                await asyncio.sleep(0.3)
                _t2 = time.time()
                # 读回验证（防假成功）：确认消息真的投递到群里
                if await self._verify_sent(cid, text):
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
        if self._fail_streak >= 3 and not _captcha_active:
            await self._restart()
        # 发送失败计数 + 持续失败告警（自愈/兜底都救不回才报，避免偶发抖动刷屏）
        if ok:
            self._consecutive_send_fail = 0
            self._send_alerted = False
        else:
            self._consecutive_send_fail += 1
            if self._consecutive_send_fail >= 3 and not self._send_alerted:
                self._send_alerted = True
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
            log("  fallback(DZMMSocket): " + ("✅" if ok else "❌") + " cid=" + cid[:8])
            return ok
        except Exception as e:
            log("  fallback err: " + str(e)[:60])
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


def should_reply(context: list[dict]) -> str:
    last_three = [m["text"] for m in context[-3:]]
    for t in last_three:
        t = t.strip()
        if t.startswith("/"):
            return t
    return ""


def _load_member_names(cid: str = None):
    """批量加载成员昵称到缓存（一次 API，替代逐个 get_user）."""
    try:
        members = get_members(cid or _ACTIVE_CID)
        for uid, name in members.items():
            if name:
                _name_cache[uid] = name
    except Exception:
        pass


def get_full_name(uid: str) -> str:
    """uid -> 完整昵称（缓存）."""
    if uid not in _name_cache:
        u = get_user(_ACTIVE_CID, uid)
        nm = (u.get("fullName", "") if u else "") or ""
        if not nm:
            # 跨群/接口失败时兜底用档案里的昵称，别把 uid 前缀当名字叫出去
            nm = str((get_profile(uid) or {}).get("name") or "")
        _name_cache[uid] = nm or uid[:8]
    return _name_cache[uid]


def get_name(uid: str) -> str:
    """uid -> 简化昵称（去掉波浪号/括号后缀）."""
    name = get_full_name(uid)
    for sep in ["～", "~", "（", "("]:
        idx = name.find(sep)
        if idx > 0:
            return name[:idx]
    return name


def _strip_parenthesis(name: str) -> str:
    """去掉昵称里括号及括号内容，保留波浪号前缀。

    如「糯糯～（裳姐姐的骚贱小母狗❤）」→「糯糯～」。
    用于发骚/续写的生成 prompt：括号里的签名（如「裳姐姐的骚贱小母狗」）
    语义太强，LLM 会把「裳姐姐」当成独立角色引入，导致内容串角色。
    """
    for sep in ["（", "("]:
        idx = name.find(sep)
        if idx > 0:
            return name[:idx]
    return name


def is_at_me(text: str) -> bool:
    """判断消息是否 @ 了小小糯（agent）."""
    if "@" not in text:
        return False
    for name in self_names():
        if name and ("@" + name) in text:
            return True
    return False


def _strip_at(text: str) -> str:
    """去掉 @小小糯 前缀，返回纯提问。"""
    for name in self_names():
        if name:
            text = text.replace("@" + name, "", 1)
    return text.strip()


def _extract_remember(text: str):
    """从自然语言提取"记住"的内容。返回内容，None=不是记忆请求或无意义内容。"""
    for kw in MEMORY_TRIGGERS:
        idx = text.find(kw)
        if idx >= 0:
            content = text[idx + len(kw):].strip()
            # 循环清理前缀残留（语气词/标点/"忆" 等）
            while True:
                cleaned = False
                for p in ["一下", "一下下", "一件事", "这件事", "这个", "要", "我", "给我",
                          "，", ",", "！", "!", "。", "？", "?", "～", "~", "忆"]:
                    if content.startswith(p):
                        content = content[len(p):].strip()
                        cleaned = True
                        break
                if not cleaned:
                    break
            content = content.strip(" ，,！!。？?～~·")
            # 过滤无意义：太短 / 纯标点 / 纯语气词
            if not content or len(content) < 2:
                return None
            if all(c in "，,！!。？?～~ 的了吗呢啊吧呀哦" for c in content):
                return None
            return content
    return None


def _get_memory(cid: str) -> list:
    """读取记忆列表（按群）。"""
    return state_store.group_get(cid, "memory", []) or []


def _remember(cid: str, text: str, from_name: str) -> None:
    """写入一条记忆（滑动窗口，最多 MEMORY_MAX 条），失败静默。"""
    try:
        mem = state_store.group_get(cid, "memory", []) or []
        mem.append({"text": text, "from": from_name,
                    "at": time.strftime("%Y-%m-%d %H:%M")})
        state_store.group_set(cid, "memory", mem[-MEMORY_MAX:])
    except Exception:
        pass


def is_remember_cmd(cmd: str):
    """识别记忆指令，返回要记的内容（空串=是但内容空），None=不是。"""
    for p in ["/记住", "/记一下", "/记", "/别忘了"]:
        if cmd.startswith(p):
            return cmd[len(p):].strip()
    return None


def is_recall_cmd(cmd: str) -> bool:
    """识别回忆指令。"""
    return any(cmd.startswith(p) for p in ["/回忆", "/记忆", "/记得什么", "/我让你记住"])


MEMORY_FILTER_PROMPT = """你是记忆筛选助手。下面给你一个「问题」和若干条「记忆」，判断哪些记忆与问题相关、能帮助回答。

输出规则：
- 只输出选中的记忆条目原文，每条一行
- 不加序号、不加解释、不改写原文
- 最多选 {limit} 条
- 一条都不相关就输出：无
"""


def _extract_names(text: str) -> list:
    """从文本提取出现的成员昵称（人名是记忆最强锚点）。"""
    found = []
    try:
        for p in load_profiles().values():
            n = p.get("name", "")
            if n and len(n) >= 2 and n in text:
                found.append(n)
    except Exception:
        pass
    return found


def _extract_keywords(text: str) -> list:
    """提取问题关键词（2-3 字片段/英文单词），用于弱匹配。"""
    clean = re.sub(r'[@～~！!？?。，,\s]+', ' ', text).strip()
    words = []
    for token in re.findall(r'[A-Za-z0-9]+|[\u4e00-\u9fa5]+', clean):
        if re.match(r'^[A-Za-z0-9]+$', token):
            if len(token) >= 2 and token not in words:
                words.append(token)
        else:
            for i in range(len(token) - 1):
                seg = token[i:i + 3]
                if len(seg) >= 2 and seg not in words:
                    words.append(seg)
        if len(words) >= 8:
            break
    return words[:8]


def _fast_rank(cid: str, question: str, top: int = 20) -> list:
    """快速粗筛：人名命中(+4) + 关键词命中(+1) 打分，取 top。"""
    mem = _get_memory(cid)
    if not mem:
        return []
    names = _extract_names(question)
    kws = _extract_keywords(question)
    scored = []
    for m in mem:
        text = m["text"]
        s = 0
        for n in names:
            if n and n in text:
                s += 4
        for kw in kws:
            if kw and kw in text:
                s += 1
        if s > 0:
            scored.append((s, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:top]]


def _ai_filter(question: str, candidates: list, limit: int = 10) -> list:
    """AI 精筛：从候选里挑与问题相关的（≤limit 条）。"""
    try:
        items = "\n".join(f"- {m['text']}" for m in candidates)
        content = f"问题：{question}\n\n记忆条目：\n{items}"
        reply = _call_llm(MEMORY_FILTER_PROMPT.format(limit=limit), content, max_tokens=500)
        picked = []
        for line in (reply or "").splitlines():
            line = line.strip().lstrip("-·•* ")
            if not line or line == "无":
                continue
            for m in candidates:
                if m["text"] == line or (len(line) >= 4 and (line in m["text"] or m["text"] in line)):
                    if m not in picked:
                        picked.append(m)
                    break
            if len(picked) >= limit:
                break
        return picked if picked else candidates[:limit]
    except Exception:
        return candidates[:limit]


def _retrieve_memory(cid: str, question: str, limit: int = 10) -> list:
    """两级检索：快速粗筛 → AI 精筛。"""
    mem = _get_memory(cid)
    if not mem:
        return []
    cand = _fast_rank(cid, question, top=20)
    if not cand:
        cand = mem[-limit:]
    if len(cand) <= limit:
        return cand
    return _ai_filter(question, cand, limit)


def _append_memory_ref(text: str, cid: str, subject: str, limit: int = 10) -> str:
    """在文本后追加与 subject 相关的记忆参考。"""
    try:
        mem = _retrieve_memory(cid, subject, limit)
        if mem:
            ref = "\n".join("· " + m["text"] for m in mem)
            return text + "\n\n（以下是小小糯记住的与" + subject + "相关的事，回答时可参考，但不要主动提'这是记忆'）\n" + ref
    except Exception:
        pass
    return text


def _find_user_in_msgs(query: str, msgs: list):
    """在已有消息列表中按昵称匹配，返回 (uid, 完整昵称)."""
    _load_member_names()  # 批量加载昵称，避免逐个 get_user
    uids = set(m.get("sent_by", "") for m in msgs)
    q = _normalize_cn(query)
    # 精确匹配优先（归一化后比较，兼容异体字/繁体/空格差异）
    for uid in uids:
        full = get_full_name(uid)
        if _normalize_cn(full) == q or _normalize_cn(get_name(uid)) == q:
            return uid, full
    # 子串匹配
    for uid in uids:
        full = get_full_name(uid)
        if q in _normalize_cn(full) or q in _normalize_cn(get_name(uid)):
            return uid, full
    return None, None


def find_user_by_name(query: str):
    """根据昵称模糊匹配，返回 (uid, 完整昵称)."""
    return _find_user_in_msgs(query, get_messages_all(_ACTIVE_CID))


def _detect_perf_query(at_msg: str):
    """检测 @ 消息是否是"谁表现怎么样"类请求，返回 (目标名, 是否今天) 或 None."""
    triggers = ("表现怎么样", "表现如何", "表现咋样", "最近怎么样", "今天怎么样",
                "最近表现", "今天表现", "最近咋样", "表现")
    if not any(t in at_msg for t in triggers):
        return None
    today = ("今天" in at_msg) and ("最近" not in at_msg)
    name = at_msg
    for n in self_names():
        if n:
            name = name.replace("@" + n, "")
    for t in triggers:
        name = name.replace(t, "")
    for w in ("今天", "最近", "的", "了", "啊", "呀", "呢", "吗", "嘛", "么",
              "怎么样", "如何", "咋样", "表现", "天气", "温度", "下雨", "下雪", "晴", "阴",
              "？", "?", "！", "!", " ", "～", "~", "，", "。", "、"):
        name = name.replace(w, "")
    name = name.strip()
    if not name:
        return None
    return name, today


def summarize_person_perf(target_name: str, today: bool) -> str:
    """按人总结表现（正常小小糯人设，不毒舌）."""
    msgs = get_messages_all(_ACTIVE_CID, max_count=200)
    uid, full = _find_user_in_msgs(target_name, msgs)
    if not uid:
        return "没找到" + target_name + "这个人哦～"
    if today:
        since = datetime.now().strftime("%Y-%m-%d") + "T00:00:00Z"
        their = _extract_msgs(uid, get_messages_since(_ACTIVE_CID, since, max_count=1000))
        label = "今天"
    else:
        their = _extract_msgs(uid, get_messages_all(_ACTIVE_CID, max_count=300))
        label = "最近"
    if not their:
        return full + label + "都没怎么说话呢～"
    text = "\n".join(their[-50:])
    try:
        summary = generate_person_summary(full, text, label)
        return (summary or "").strip().replace("🤖", "")
    except Exception:
        return full + label + "聊得挺热闹的，不过我这边整理得有点乱～"


def get_group_name() -> str:
    """查询群名（5分钟缓存，按群隔离）."""
    cid = _ACTIVE_CID
    cache = _group_name_cache.get(cid, {"name": "", "ts": 0.0})
    if time.time() - cache["ts"] < 300 and cache["name"]:
        return cache["name"]
    cache["name"] = get_chatroom_name(cid)
    cache["ts"] = time.time()
    _group_name_cache[cid] = cache
    return cache["name"]


def is_summary_cmd(cmd: str) -> bool:
    """判断是否是要总结群聊记录的指令."""
    keywords = ["总结", "回顾", "昨天", "今天聊", "最近聊", "记录",
                "聊了什么", "情况", "复盘", "发生", "讨论", "整理"]
    return any(k in cmd for k in keywords)


HELP_TEXT = """小小糯的指令说明～：
@小小糯～ - 直接@我聊天
/骚度排行 - 发情值排行
/发骚 /体位骰 - 涩涩骰子
/羞辱 昵称 - 恶魔羞辱 /吐槽开/关 - 主动吐槽
/继续 - 续写  /算命 /占卜 - 运势
/注册 - 建档案  /查 昵称 - 查档案
/公演开始 /公演结束 - 公演+小说
/剧情开始 /投票 - 游戏&投票(/选 N)
🔥 发情值满100触发高潮"""

WELCOME_TEXT = """🎉 欢迎 {name} 姐姐加入～♡ 我是小小糯～，群里的小秘书～

💬 新人先冒个泡说句话，让姐姐们认识认识你呀～还没注册的话发 /注册 建个档案哦～📝

⚠️ 本群是妹子认证群，不是妹子的小哥哥会被踢出去哦～🚫（没认证的可以私聊糯糯姐发照片认证，哈哈开玩笑的～）

【群简介】姐妹们发骚聊天的地方～可以 🎲发骚、🔗玩调教、😈被羞辱、🎭开公演，还有 🎬剧情游戏、📊投票、🔮算命、📇查档案、🔥发情值排行～发 /说明 看全部功能～♡"""

# 非认证群的欢迎语（中性版）：不写"会被踢""认证"等字样，只介绍功能
WELCOME_TEXT_PLAIN = """🎉 欢迎 {name} 加入～♡ 我是小小糯～，群里的小秘书～

💬 冒个泡说句话，让大家认识认识你呀～还没注册的话发 /注册 建个档案哦～📝

【群简介】小小糯会 🔮算命、📇查档案、📝写诗、🎬剧情游戏、📊投票、🎲骰子、🔥发言排行，也能陪你聊天～发 /说明 看全部功能～♡"""

# 新成员筛查（妹子认证群）：踢游客 / 踢男性 / 提醒未填性别
TOURIST_KEYWORDS = ["游客"]
JOIN_WARN_TOURIST = "⚠️ @{name} 你是游客账号，本群是妹子认证群，稍后会将你移出哦～"
JOIN_DONE_TOURIST = "🚫 已移出游客账号「{name}」～（请用正式账号加入）"
JOIN_WARN_MALE = "⚠️ @{name} 检测到你的性别是男，本群是妹子认证群，稍后会将你移出哦～"
JOIN_DONE_MALE = "🚫 已移出「{name}」（原因：男性），本群是妹子认证群～"
JOIN_WARN_NULL = "⚠️ 新成员「{name}」加入但未填性别资料，请管理员留意认证～ @{name} 也请尽快完善资料哦～"
JOIN_WARN_NOT_VERIFIED = "⚠️ 新成员「{name}」加入但未通过妹子认证，请管理员留意～ @{name} 也请尽快完成认证哦～"

# 运势磕头道歉文案（2026-09-05 赔罪日追加在运势分析后）
FORTUNE_APOLOGY = """🙇♀️🙇♀️🙇♀️ 姐姐们，小小糯来磕头认错啦～前几天把运势算得凶凶的，是小小糯不好，这就跪下咚咚咚给姐姐们磕响头赔罪～💦
为了赔罪，今天每一位姐姐的运势，小小糯都保证是 90 分以上的大吉～🍀 祝姐姐们好运连连，天天被幸福包围～
姐姐们就原谅软软糯糯的小小糯这一次嘛～好不好嘛～♡"""

# 记忆功能：触发词 + 上限
MEMORY_TRIGGERS = ["帮我记住", "帮我记", "记住", "记一下", "记着", "别忘了", "牢记"]
MEMORY_MAX = 100

INTRO_TEXT = """我是小小糯～19岁，糯糯姐的私人秘书，银白双马尾配红蓝异色瞳，软软糯糯的小奶猫一只～♡
我会查档案、排骚度、羞辱人、总结群聊，还能陪姐姐们聊天～
还没注册的姐姐，发 /注册 建立档案，以后查你会更丰富哦～发 /说明 看详细功能～"""

def is_help_cmd(cmd: str) -> bool:
    """识别帮助指令."""
    return any(cmd.startswith(p) for p in ["/帮助", "/help", "/指令", "/菜单", "/命令"])


def is_devil_cmd(cmd: str):
    """识别主动吐槽开关指令，返回 'on'/'off'/None."""
    c = cmd.strip()
    if c.startswith("/吐槽开"):
        return "on"
    if c.startswith("/吐槽关"):
        return "off"
    return None


def is_intro_cmd(cmd: str) -> bool:
    """识别自我介绍指令."""
    return any(cmd.startswith(p) for p in ["/自我介绍", "/介绍", "/你是谁", "/whoami"])


def is_guide_cmd(cmd: str) -> bool:
    """识别使用说明指令."""
    return any(cmd.startswith(p) for p in ["/说明", "/使用说明", "/功能", "/指南"])


def is_sync_cmd(cmd: str) -> bool:
    """识别同步指令."""
    return cmd.startswith("/同步") or cmd.startswith("/sync")


def is_register_cmd(cmd: str) -> str:
    """识别注册指令，返回目标昵称（空串=注册自己，None=不是）."""
    for prefix in ["/注册", "/register"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None


def _extract_xp(profile_text: str) -> list:
    """提炼并解析 XP 标签（返回字符串列表）."""
    try:
        raw = (generate_xp(profile_text) or "").strip()
        if not raw:
            return []
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            arr = json.loads(raw[start:end + 1])
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        pass
    return []


def register_user(query: str, sender_uid: str) -> str:
    """注册用户，返回结果文本."""
    msgs = get_messages_all(_ACTIVE_CID)  # 拉一次，找用户+发言都复用
    if query:
        # 代注册：只允许糯糯（群主）
        if sender_uid != OWNER_UID:
            return "只有糯糯姐才能帮别人注册哦～你自己发 /注册 就可以啦～"
        uid, full = _find_user_in_msgs(query, msgs)
        if not uid:
            return "没找到这个人呢，注册不了～"
    else:
        uid = sender_uid
        full = get_full_name(uid)

    # 已注册则幂等：不重复建档，直接提示
    existing = get_profile(uid)
    if existing:
        return "✅ " + full + " 已经注册过啦～不用重复注册哦，发 /查 " + full + " 就能看档案啦～"

    u = get_user(_ACTIVE_CID, uid)
    profile_text = _member_profile_text(uid, full, msgs)
    summary = generate_profile_summary(profile_text)
    xp = _extract_xp(profile_text)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profile = {
        "name": full,
        "gender": str(u.get("gender")) if u and u.get("gender") else "",
        "signature": (u.get("quirk") or "") if u else "",
        "bio": (u.get("bio") or "") if u else "",
        "registered_at": now,
        "register_summary": summary,
        "xp": xp,
        "last_seen": now,
        "query_count": 0,
    }
    if upsert_profile(uid, profile):
        return "✅ " + full + " 注册成功啦～已经记进档案，以后查" + full + "会更丰富哦～"
    return "注册失败，存档出了点问题～"


def is_profile_cmd(cmd: str) -> str:
    """识别查人指令，返回目标昵称（空串表示不是）."""
    for prefix in ["/查", "/whois", "/资料"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return ""


def is_rank_cmd(cmd: str) -> bool:
    """识别骚度排行指令."""
    keywords = ["骚度", "排行", "骚榜", "骚排名", "骚气", "谁最骚", "骚王"]
    return any(k in cmd for k in keywords)


def rank_summary() -> str:
    """生成骚度排行（高潮次数为主，发情值为辅，活跃度不参与排名，排除小小糯）."""
    cid = _ACTIVE_CID
    group_state = (load_state() or {}).get(cid) or {}
    heat = group_state.get("heat_value") or {}
    counts = group_state.get("msg_count") or {}
    climax = group_state.get("heat_climax") or {}
    today_climax = group_state.get("heat_climax_today") or {}
    uids = set(heat) | set(counts) | set(climax)
    uids.difference_update(self_uids())
    if not uids:
        return ""
    _load_member_names()
    items = sorted(uids, key=lambda u: (-today_climax.get(u, 0), -climax.get(u, 0), -heat.get(u, 0), -counts.get(u, 0)))
    lines = ["🔥 骚度排行"]
    for i, uid in enumerate(items, 1):
        name = get_name(uid)  # 简化名字（去掉括号/波浪号后缀）
        h = heat.get(uid, 0)
        c = counts.get(uid, 0)
        cl = climax.get(uid, 0)
        ct = today_climax.get(uid, 0)
        line = name + " · 高潮今" + str(ct) + "/总" + str(cl) + " · 发情" + str(h) + " · 活跃" + str(c)
        if i == 1:
            lines.append("🥇 " + line)
        elif i == 2:
            lines.append("🥈 " + line)
        elif i == 3:
            lines.append("🥉 " + line)
        else:
            lines.append(str(i) + ". " + line)
    return "\n".join(lines)


# ============ 发情值增量（P8 集中管理）============
# 原先 5/8/10/15/20 散落在十余个函数里，改规则要到处找。括号内是原行号，便于对照。
HEAT_SCENE_PICK = 10              # 发骚骰子：被抽中（2887）
HEAT_SCENE_CONTINUE_TARGET = 10   # 发骚续写：目标（3028）
HEAT_SCENE_CONTINUE_SENDER = 10   # 发骚续写：发起人（3030）
HEAT_INSULT_TARGET = 15           # 被羞辱（3056、4921）
HEAT_INSULT_SENDER = 10           # 羞辱续写：发起人（3058）
HEAT_FASAO_PICK = 15              # 被点名发骚 /发情点名（1680）
HEAT_DIAOJIAO_ROUND = 20          # 被调教一轮（3290）
HEAT_VOTE_STARTER = 8             # 投票：发起人（3562）
HEAT_VOTE_VOTER = 5               # 投票：参与者（3565）
HEAT_STORY_VOTER = 8              # 剧情：投票者（3746）
HEAT_STORY_PROTAGONIST = 15       # 剧情：主角（3748）


def _add_heat(cid: str, uid: str, delta: int) -> None:
    """给某人加发情值（持久化，排除小小糯）."""
    if not uid or is_self(uid) or delta <= 0:
        return
    # P8：单键读写 —— 只写该群那一行（原先每次都要重写全部群）
    heat = state_store.group_get(cid, "heat_value", {}) or {}
    heat[uid] = heat.get(uid, 0) + delta
    state_store.group_set(cid, "heat_value", heat)


def _rollback_last_id(cid: str, old_last_id: str) -> None:
    """生成失败（网络超时/空回复）时回滚 last_id，让这条消息下轮重试."""
    state = load_state()
    group_state = state.get(cid) or {}
    group_state["last_id"] = old_last_id
    state[cid] = group_state
    save_state(state)


def _climax_hits(group_state: dict):
    """检测发情值≥100，返回 (hits, changed)。changed=有跨天重置或高潮（需写回磁盘）."""
    heat = group_state.get("heat_value") or {}
    climax = group_state.get("heat_climax") or {}
    today_climax = group_state.get("heat_climax_today") or {}
    today = datetime.now().strftime("%Y-%m-%d")
    changed = False
    # 跨天重置今日高潮次数
    if group_state.get("climax_date") != today:
        today_climax = {}
        group_state["climax_date"] = today
        changed = True
    hits = []
    for uid, h in list(heat.items()):
        if h >= 100:
            hits.append((uid, h))
            heat[uid] = h - 100
            climax[uid] = climax.get(uid, 0) + 1
            today_climax[uid] = today_climax.get(uid, 0) + 1
            changed = True
    if changed:
        group_state["heat_value"] = heat
        group_state["heat_climax"] = climax
        group_state["heat_climax_today"] = today_climax
    return hits, changed


def _parse_judge(result: str) -> dict:
    """解析 AI 评定结果 JSON，返回 {name: delta}."""
    import json as _json
    import re as _re
    result = (result or "").strip()
    result = _re.sub(r"```(?:json)?", "", result).strip()
    data = None
    try:
        data = _json.loads(result)
    except Exception:
        m = _re.search(r"\[.*\]", result, _re.DOTALL)
        if m:
            try:
                data = _json.loads(m.group(0))
            except Exception:
                data = None
    out = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                try:
                    delta = int(item.get("delta", 0))
                except Exception:
                    delta = 0
                if name and delta > 0:
                    out[name] = delta
    return out


def _find_uid_by_name(name: str) -> str:
    """昵称→uid。精确匹配优先，单字不模糊匹配（避免「裳」误中「裳姐姐」）。"""
    name = (name or "").strip()
    if not name:
        return ""
    # 1. 完整昵称精确匹配
    for uid, full in _name_cache.items():
        if full == name:
            return uid
    # 2. 简化昵称精确匹配（去括号/波浪号）
    for uid in _name_cache:
        if get_name(uid) == name:
            return uid
    # 3. 前缀匹配（name 是完整昵称前缀，如「糯糯」匹配「糯糯～（...）」）
    for uid, full in _name_cache.items():
        if full.startswith(name):
            return uid
    # 4. 子串匹配（仅 name 长度 ≥2，单字「裳」不参与，避免误中「裳姐姐」）
    if len(name) >= 2:
        for uid, full in _name_cache.items():
            if name in full or full in name:
                return uid
    # 档案同样精确优先
    profiles = load_profiles()
    for uid, p in profiles.items():
        if p.get("name", "") == name:
            return uid
    for uid, p in profiles.items():
        full = p.get("name", "")
        if full and full.startswith(name):
            return uid
    if len(name) >= 2:
        for uid, p in profiles.items():
            full = p.get("name", "")
            if full and (name in full or full in name):
                return uid
    return ""


def _judge_pending(cid: str) -> str:
    """批量评定待评发言的发情值（每2小时调用），返回评定结果文本."""
    state = load_state()
    group_state = state.get(cid) or {}
    pending = group_state.get("heat_pending") or {}
    if not pending:
        return ""
    _load_member_names()
    lines = []
    for uid, texts in pending.items():
        name = get_full_name(uid)
        lines.append(name + ": " + " | ".join(texts))
    chat_text = "\n".join(lines)
    try:
        result = generate_heat_judge(chat_text)
    except Exception:
        result = ""
    result = (result or "").strip()
    delta = _parse_judge(result)
    heat = group_state.get("heat_value") or {}
    for name, d in delta.items():
        uid = _find_uid_by_name(name)
        if uid and not is_self(uid):
            heat[uid] = heat.get(uid, 0) + d
    group_state["heat_value"] = heat
    group_state["heat_pending"] = {}
    group_state["heat_last_judge"] = time.time()
    state[cid] = group_state
    save_state(state)
    return result


def _build_climax_context(cid: str, max_count: int = 60) -> str:
    """拉最近群聊内容，组装成「昵称: 内容」上下文，供高潮生成判断场景/施动者。"""
    _load_member_names()
    try:
        msgs = get_messages_all(cid, max_count=max_count)
    except Exception:
        return ""
    lines = []
    for m in msgs[-max_count:]:
        uid = m.get("sent_by", "")
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text or text.startswith("/"):
            continue
        name = get_full_name(uid) if uid else ""
        if not name or name == uid[:8]:
            continue  # 拿不到昵称的跳过，避免 uid 干扰
        lines.append(name + ": " + text[:60])
    return "\n".join(lines)


async def _handle_climax(sender, cid: str, dry: bool, hits: list):
    """处理高潮事件：为每个发情值≥100的成员生成专属色色文字并 @当事人（标注今日/总高潮次数）."""
    gs = (load_state() or {}).get(cid) or {}
    total_map = gs.get("heat_climax") or {}
    today_map = gs.get("heat_climax_today") or {}
    context = _build_climax_context(cid)  # 拉一次上下文，循环复用
    for uid, _h in hits:
        name = get_full_name(uid)
        total = total_map.get(uid, 0)
        today = today_map.get(uid, 0)
        profile = get_profile(uid)
        user_text = _archive_text_from_profile(profile) if profile else "昵称：" + name
        if context:
            user_text += "\n\n【最近群聊上下文】\n" + context
        try:
            text = generate_heat_climax(user_text)
        except Exception:
            text = ""
        text = (text or "").strip().replace("🤖", "")
        tag = "🔥 发情值爆表！今天第" + str(today) + "次高潮，总共第" + str(total) + "次～"
        if text:
            msg = "@" + name + " " + tag + "\n\n" + text
        else:
            msg = "@" + name + " " + tag
        if dry:
            log("[dry] " + msg[:60])
        else:
            ok = await sender.send("🤖 " + msg, cid)
            log("✅" if ok else "❌")
        await asyncio.sleep(1.0)


async def _publish_rank(sender, cid: str, dry: bool):
    """发布骚度排行."""
    rank = rank_summary()
    if not rank:
        return
    if dry:
        log("[dry] " + rank[:60])
    else:
        ok = await sender.send("🤖 " + rank, cid)
        log("✅" if ok else "❌")


async def _publish_memory_report(sender, cid: str, dry: bool):
    """每日记忆汇报：列出具体记忆内容（原文列表）。"""
    mem = _get_memory(cid)
    if not mem:
        return
    total = len(mem)
    shown = mem[-8:]
    lines = [f"📝 小小糯今日记忆（共 {total} 条）："]
    for i, m in enumerate(shown, 1):
        at_short = (m.get('at') or '')
        if len(at_short) >= 11:
            at_short = at_short[5:]
        lines.append(f"{i}. {m['text']}（{m.get('from', '?')}，{at_short}）")
    if total > len(shown):
        lines.append(p("mem_list_more", cid).format(k=len(shown)))
    text = "\n".join(lines)
    if dry:
        log("[dry] 记忆汇报")
    else:
        ok = await sender.send("🤖 " + text, cid)
        log("✅" if ok else "❌")


# ============ 吹牛骰子游戏 ============

DICE_TIMEOUT_SEC = 180  # 3 分钟超时弃权


def _dice_state() -> dict:
    """读骰子局状态（**顶层键**，不按 cid 存）。"""
    return state_store.top_get("dice_game") or {}


def _dice_save(g: dict) -> None:
    """写骰子局状态。"""
    state_store.top_set("dice_game", g)


def _dice_current_player(g: dict) -> str:
    """当前轮到谁（order[current_idx]）。"""
    order = g.get("order") or []
    idx = g.get("current_idx", 0)
    if not order:
        return ""
    return order[idx % len(order)]


async def _dice_send_private(sender, uid: str, text: str) -> bool:
    """私聊发消息给 uid（纯 Playwright，不走 Socket.IO 兜底，避免假成功）。"""
    cid = await asyncio.to_thread(get_private_cid, uid)
    if not cid:
        log("  私聊 chat_id 未找到 uid=" + uid[:8])
        return False
    return await sender.send(text, cid, allow_fallback=False)


def _dice_board_text(g: dict, final: bool = False) -> str:
    """看板：轮结束看板 或 总看板。"""
    players = g.get("players", [])
    stats = g.get("stats", {})
    round_num = g.get("round", 1)
    lines = []
    if final:
        lines.append(f"📊 骰子局结束！共 {round_num} 轮，参与 {len(players)} 人")
    else:
        lines.append(f"📊 第 {round_num} 轮结束（当前 {len(players)} 人）")
    lines.append("参与人员：" + "、".join("@" + get_name(p) for p in players))
    lines.append("累计战绩：")
    for p in players:
        s = stats.get(p, {})
        lines.append(f"@{get_name(p)}  {s.get('win', 0)}胜{s.get('lose', 0)}负")
    if not final:
        lines.append("发 /下一轮 开下一轮（没在发骚时 /继续 也行），发起者可 /结束")
    return "\n".join(lines)


async def _dice_deal_round(sender, g: dict) -> str:
    """发骰 + 返回本轮广播文本（编号 + 首叫提示 + 发骰失败提示）。"""
    order = g.get("order", [])
    failed = []
    for p in order:
        ok = await _dice_send_private(sender, p, "你的骰子：" + dice_str(g["dice"][p]))
        log("  发骰 " + get_name(p) + ": " + ("✅" if ok else "❌"))
        if not ok:
            failed.append(p)
    num = len(order)
    lines = [f"🎲 骰子局（第 {g.get('round', 1)} 轮）开始！"]
    wild = g.get("wild")
    if wild:
        lines.append(f"🃏 本轮万能点：{dice_str([wild])}（可顶任何点数）")
    for i, p in enumerate(order, 1):
        lines.append(f"{i}号 @{get_name(p)}")
    lines.append(f"由 1号 首叫（最少叫 {num} 个，格式 /N个X）")
    if failed:
        lines.append("⚠️ " + "、".join("@" + get_name(p) for p in failed)
                     + " 骰子没发成功（私聊未解锁），请先私聊小小糯发一句话，再发 /看骰 查看～")
    return "\n".join(lines)


async def _dice_start_cmd(cid: str, uid: str) -> str:
    """发起骰子局。"""
    g = _dice_state()
    if g.get("active"):
        return "🎲 已经有一局骰子在进行啦～先 /结束 再开新的～"
    g = {"active": True, "phase": "waiting", "host": uid, "cid": cid,
         "players": [uid], "order": [], "dice": {},
         "current_call": None, "last_caller": None, "wild": None, "called_wild": False,
         "current_idx": 0, "last_action_ts": time.time(), "timeout_streak": 0,
         "round": 1, "stats": {}, "round_history": []}
    _dice_save(g)
    return "🎲 吹牛骰子局发起！想玩的发 /加入，人齐后发起者发 /开始～"


async def _dice_join_cmd(uid: str) -> str:
    """加入骰子局（等待中立即加入；游戏中加入下一轮生效）。"""
    g = _dice_state()
    if not g.get("active"):
        return "现在没有骰子局哦～发 /吹牛骰子 发起～"
    if uid in g.get("players", []):
        return "你已经在局里啦～"
    if g.get("phase") == "waiting":
        g["players"].append(uid)
        _dice_save(g)
        return f"@{get_name(uid)} 加入！当前 {len(g['players'])} 人～"
    # 游戏中加入，下一轮生效（本轮不参与 order）
    g["players"].append(uid)
    _dice_save(g)
    return f"@{get_name(uid)} 加入！下一轮参战～"


def _dice_exit_cmd(uid: str) -> str:
    """退出游戏（不算输）。"""
    g = _dice_state()
    if not g.get("active") or uid not in g.get("players", []):
        return ""
    old_order = g.get("order", [])
    old_idx = g.get("current_idx", 0) % max(len(old_order), 1)
    old_cur = old_order[old_idx] if old_order else ""
    g["players"].remove(uid)
    new_order = [p for p in old_order if p != uid]
    g["order"] = new_order
    if new_order:
        if old_cur == uid:
            g["current_idx"] = min(old_idx, len(new_order) - 1)
        elif old_cur in new_order:
            g["current_idx"] = new_order.index(old_cur)
        else:
            g["current_idx"] = 0
    else:
        g["current_idx"] = 0
    _dice_save(g)
    if len(g["players"]) < 2:
        g["active"] = False
        g["phase"] = "done"
        _dice_save(g)
        return f"@{get_name(uid)} 退出游戏，剩余不足 2 人，骰子局结束。"
    return f"@{get_name(uid)} 退出游戏（不算输），剩余 {len(g['players'])} 人。"


async def _dice_begin_cmd(sender, uid: str) -> str:
    """开始骰子局（发骰 + 随机编号）。"""
    g = _dice_state()
    if not g.get("active"):
        return "现在没有骰子局哦～发 /吹牛骰子 发起～"
    if uid != g.get("host"):
        return "只有发起者能 /开始 哦～"
    if g.get("phase") != "waiting":
        return "已经开始了～"
    if len(g.get("players", [])) < 2:
        return "至少 2 人才能开始哦～"
    import random
    players = list(g["players"])
    random.shuffle(players)
    g["order"] = players
    g["dice"] = {p: roll_dice(5) for p in players}
    g["phase"] = "calling"
    g["current_call"] = None
    g["last_caller"] = None
    g["wild"] = roll_wild()
    g["called_wild"] = False
    g["current_idx"] = 0
    g["last_action_ts"] = time.time()
    g["timeout_streak"] = 0
    g["round"] = 1
    g["stats"] = {}
    g["round_history"] = []
    _dice_save(g)
    return await _dice_deal_round(sender, g)


async def _dice_next_round_cmd(sender) -> str:
    """/下一轮（或发骚未进行时的 /继续）：进入下一轮（新加入者追加到顺序，重新摇骰发骰）。"""
    g = _dice_state()
    if not g.get("active") or g.get("phase") != "round_end":
        return ""
    # 新加入的人追加到 order 末尾
    order = list(g.get("order", []))
    for p in g.get("players", []):
        if p not in order:
            order.append(p)
    g["order"] = order
    g["round"] = g.get("round", 1) + 1
    g["dice"] = {p: roll_dice(5) for p in order}
    g["phase"] = "calling"
    g["current_call"] = None
    g["last_caller"] = None
    g["wild"] = roll_wild()
    g["called_wild"] = False
    g["current_idx"] = 0
    g["last_action_ts"] = time.time()
    g["timeout_streak"] = 0
    _dice_save(g)
    return await _dice_deal_round(sender, g)


async def _dice_call_cmd(uid: str, call) -> str:
    """叫数加码。"""
    g = _dice_state()
    if not g.get("active") or g.get("phase") != "calling":
        return "现在没有进行中的骰子局哦～"
    cur = _dice_current_player(g)
    if uid != cur:
        return f"还没轮到你哦～现在轮到 @{get_name(cur)}～"
    if not is_legal_raise(g.get("current_call"), call, len(g.get("order", []))):
        return "叫数不合法哦～要比上一个大（个数更多，或点数更大）～"
    g["current_call"] = list(call)
    g["last_caller"] = uid
    if call[1] == g.get("wild"):
        g["called_wild"] = True
    g["current_idx"] = (g.get("current_idx", 0) + 1) % len(g["order"])
    g["last_action_ts"] = time.time()
    g["timeout_streak"] = 0  # 正常行动重置超时计数
    _dice_save(g)
    nxt = _dice_current_player(g)
    n, x = call
    tip = "（🎲万能点失效！）" if x == g.get("wild") else ""
    return f"@{get_name(uid)} 叫 {n}个{x}{tip}！轮到 @{get_name(nxt)}（加码 /N个X 或 /开）"


async def _dice_open_cmd(uid: str) -> str:
    """开牌判定：记输赢 → 看板 → round_end。"""
    g = _dice_state()
    if not g.get("active") or g.get("phase") != "calling":
        return "现在没有进行中的骰子局哦～"
    cur = _dice_current_player(g)
    if uid != cur:
        return f"还没轮到你哦～现在轮到 @{get_name(cur)}～"
    if not g.get("current_call"):
        return "还没有人叫数呢，不能开～"
    caller = g.get("last_caller")
    if caller == uid:
        return "你不能开自己叫的数哦～"
    call = tuple(g["current_call"])
    actual = count_point(g["dice"], call[1], g.get("wild", 1), g.get("called_wild", False))
    caller_win = actual >= call[0]
    winner = caller if caller_win else uid
    loser = uid if caller_win else caller
    # 记录输赢
    stats = g.get("stats", {})
    stats.setdefault(winner, {"win": 0, "lose": 0})["win"] += 1
    stats.setdefault(loser, {"win": 0, "lose": 0})["lose"] += 1
    g["stats"] = stats
    g.setdefault("round_history", []).append(
        {"round": g.get("round", 1), "winner": winner, "loser": loser, "actual": actual})
    g["phase"] = "round_end"
    g["last_action_ts"] = time.time()
    _dice_save(g)
    lines = ["🎲 开牌！全场骰子："]
    for p in g.get("order", []):
        lines.append(f"@{get_name(p)}：{dice_str(g['dice'][p])}")
    n, x = call
    lines.append(f"@{get_name(caller)} 叫 {n}个{x}，实际 {actual} 个")
    lines.append(f"🎉 @{get_name(winner)} 赢！@{get_name(loser)} 输了，接受惩罚～")
    lines.append("")
    lines.append(_dice_board_text(g))
    return "\n".join(lines)


async def _dice_quit_cmd(uid: str, timeout: bool = False) -> str:
    """主动 /结束：等待阶段取消，游戏中出总看板。"""
    g = _dice_state()
    if not g.get("active"):
        return ""
    if g.get("phase") == "waiting":
        g["active"] = False
        g["phase"] = "done"
        _dice_save(g)
        return "🎲 骰子局已取消～"
    if g.get("phase") not in ("calling", "round_end"):
        return ""
    g["active"] = False
    g["phase"] = "done"
    _dice_save(g)
    return _dice_board_text(g, final=True)


def _dice_skip_cmd(uid: str) -> str:
    """超时跳过：uid 移到顺序最后，游戏继续。连续超时满一圈则本轮作废。"""
    g = _dice_state()
    if not g.get("active") or g.get("phase") != "calling":
        return ""
    order = g.get("order", [])
    n = len(order)
    if n == 0:
        return ""
    idx = g.get("current_idx", 0) % n
    if order[idx] != uid:
        return ""
    # uid 移到最后，current_idx 指向原下一位（最后一位超时则回到 0）
    new_order = order[:idx] + order[idx + 1:] + [order[idx]]
    g["order"] = new_order
    g["current_idx"] = 0 if idx == n - 1 else idx
    g["last_action_ts"] = time.time()
    g["timeout_streak"] = g.get("timeout_streak", 0) + 1
    # 连续超时满一圈（全场轮空）→ 本轮作废，进入 round_end
    if g["timeout_streak"] >= n:
        g["phase"] = "round_end"
        g["last_action_ts"] = time.time()
        _dice_save(g)
        return "⏱️ 大家都超时了，这一轮作废！\n\n" + _dice_board_text(g)
    _dice_save(g)
    nxt = _dice_current_player(g)
    return f"⏱️ @{get_name(uid)} 超时，自动跳到下一位！轮到 @{get_name(nxt)}"


def _dice_status_text(g: dict) -> str:
    """骰子局状态文本。"""
    if not g.get("active"):
        return "现在没有进行中的骰子局哦～发 /吹牛骰子 发起～"
    if g.get("phase") == "waiting":
        return f"🎲 骰子局等待中（{len(g.get('players', []))} 人），发起者发 /开始～"
    if g.get("phase") == "round_end":
        return f"🎲 第 {g.get('round', 1)} 轮已结束，发 /下一轮 开下一轮（没在发骚时 /继续 也行）～"
    order = g.get("order", [])
    cur = _dice_current_player(g)
    call = g.get("current_call")
    lines = [f"🎲 骰子局第 {g.get('round', 1)} 轮进行中（{len(order)} 人）"]
    lines.append("顺序：" + " → ".join(f"{i+1}号@{get_name(p)}" for i, p in enumerate(order)))
    if call:
        n, x = call
        lines.append(f"当前叫数：{n}个{x}（@{get_name(g.get('last_caller', ''))} 叫的）")
    else:
        lines.append("当前叫数：无（等 1号 首叫）")
    lines.append(f"轮到：@{get_name(cur)}")
    wild = g.get("wild")
    if wild:
        if g.get("called_wild"):
            lines.append(f"⚠️ 已叫过万能点 🎲{wild}，它不再万能")
        else:
            lines.append(f"🃏 本轮万能点：🎲{wild}（可顶任何点数）")
    return "\n".join(lines)


def is_fasao_cmd(cmd: str) -> bool:
    """识别发骚点名指令."""
    return cmd.startswith("/发骚") or cmd.startswith("/发情点名")


def fasao(cmd: str) -> str:
    """发骚点名：随机抽或指定某人发骚，加发情值."""
    cid = _ACTIVE_CID
    target = ""
    for prefix in ["/发骚", "/发情点名"]:
        if cmd.startswith(prefix):
            target = cmd[len(prefix):].strip().lstrip("@").strip()
            break
    uid = None
    full = ""
    if target:
        uid, _p = _find_in_profiles(target)
        if uid:
            full = get_full_name(uid)
        else:
            msgs = get_messages_all(cid, max_count=200)
            uid, full = _find_user_in_msgs(target, msgs)
        if not uid:
            return "没找到这个人哦～"
    else:
        uid = _random_member_uid()
        full = get_full_name(uid) if uid else ""
        if not uid:
            return "群里还没有可以点名的人～"
    try:
        text = generate_heat_fasao(full)
    except Exception:
        text = ""
    text = (text or "").strip().replace("🤖", "")
    if not text:
        text = full + "，发个骚给主人看看～"
    _add_heat(cid, uid, HEAT_FASAO_PICK)
    return "🎯 点中 " + full + "！\n" + text


def _extract_msgs(uid: str, msgs: list) -> list:
    """从已有消息列表过滤某成员的发言文本."""
    their_msgs = []
    for m in msgs:
        if m.get("sent_by") == uid:
            text = (m.get("content") or {}).get("text", "").strip()
            if text and "加入了群聊" not in text:
                their_msgs.append(text)
    return their_msgs


def _get_msgs_by_uid(uid: str) -> list:
    """拉取某成员的所有发言文本."""
    return _extract_msgs(uid, get_messages_all(_ACTIVE_CID))


def _member_profile_text(uid: str, full: str, msgs: list = None) -> str:
    """组装某成员资料+发言文本."""
    u = get_user(_ACTIVE_CID, uid)
    their_msgs = _extract_msgs(uid, msgs) if msgs is not None else _get_msgs_by_uid(uid)
    lines = ["昵称：" + full]
    if u:
        if u.get("gender"):
            lines.append("性别：" + str(u["gender"]))
        if u.get("quirk"):
            lines.append("签名：" + str(u["quirk"]))
        if u.get("bio"):
            lines.append("简介：" + str(u["bio"]))
    if their_msgs:
        lines.append("最近发言（" + str(len(their_msgs)) + "条）：")
        for t in their_msgs[-30:]:
            lines.append("  - " + t[:80])
    return "\n".join(lines)


def profile_summary(query: str) -> str:
    """查资料+发言，返回给 AI 的文本."""
    uid, full = find_user_by_name(query)
    if not uid:
        return ""
    return _member_profile_text(uid, full)


# ---- 中文昵称归一化（繁→简 + 异体字→标准字 + 去空白）----
# 用于昵称模糊匹配，解决"寜/寧"异体字、"繁简混用"、空格差异导致的匹配失败
_OPENCC_T2S = None


def _get_opencc():
    """懒加载 opencc t2s 转换器（首次调用才创建，失败返回 None 跳过繁简转换）。"""
    global _OPENCC_T2S
    if _OPENCC_T2S is None:
        try:
            from opencc import OpenCC
            _OPENCC_T2S = OpenCC('t2s')
        except Exception:
            _OPENCC_T2S = False  # 不可用，跳过繁简转换
    return _OPENCC_T2S or None


# opencc t2s 不覆盖的生僻异体字 → 简体标准字
_CJK_VARIANT = {
    '寜': '宁', '寕': '宁', '甯': '宁',
    '爲': '为', '為': '为',
    '夠': '够', '峯': '峰', '羣': '群',
    '裏': '里', '裡': '里', '佈': '布', '併': '并',
    '兇': '凶', '佔': '占', '週': '周', '嚮': '向',
    '麪': '面', '迴': '回', '姦': '奸', '啓': '启',
    '敍': '叙', '妳': '你', '媮': '偷',
}


def _normalize_cn(s: str) -> str:
    """中文昵称归一化：NFKC + 异体字映射 + 繁→简 + 去空白 + 大小写折叠。"""
    if not s:
        return ""
    s = unicodedata.normalize('NFKC', s)
    s = ''.join(_CJK_VARIANT.get(ch, ch) for ch in s)
    cc = _get_opencc()
    if cc:
        try:
            s = cc.convert(s)
        except Exception:
            pass
    return re.sub(r'\s+', '', s).casefold()


def _simplify(name: str) -> str:
    """简化昵称：去掉 ～/~/（ 之后的后缀."""
    for sep in ["～", "~", "（", "("]:
        idx = name.find(sep)
        if idx > 0:
            return name[:idx]
    return name


def _find_in_profiles(query: str):
    """在档案里按昵称匹配，返回 (uid, profile) 或 (None, None)."""
    profiles = load_profiles()
    q = _normalize_cn(query)
    for uid, p in profiles.items():
        name = p.get("name", "")
        if _normalize_cn(name) == q or _normalize_cn(_simplify(name)) == q:
            return uid, p
    for uid, p in profiles.items():
        if q in _normalize_cn(p.get("name", "")):
            return uid, p
    return None, None


def _archive_text_from_profile(profile: dict) -> str:
    """从档案构建查询文本（零 API）."""
    lines = ["【注册档案】", "昵称：" + profile.get("name", "")]
    if profile.get("gender"):
        lines.append("性别：" + profile["gender"])
    if profile.get("signature"):
        lines.append("签名：" + profile["signature"])
    if profile.get("bio"):
        lines.append("简介：" + profile["bio"])
    if profile.get("xp"):
        lines.append("XP性癖：" + "、".join(profile["xp"]))
    lines.append("注册时间：" + profile.get("registered_at", ""))
    hl = _history_lines(profile, n=3, bio_n=70)
    if hl:
        lines.append("【变化轨迹（姓名/签名/简介；越靠前越早，可挑一处做对比反差）】")
        lines.extend(hl)
    lines.append("注册时画像：" + profile.get("register_summary", ""))
    if profile.get("summary"):
        lines.append("滚动总结：" + profile["summary"])
    msgs = profile.get("messages", [])
    if msgs:
        lines.append("【最新发言】")
        for m in msgs[-10:]:
            lines.append("  - [" + m.get("at", "") + "] " + m.get("text", "")[:60])
    return "\n".join(lines)


def _live_insult_text(uid: str, profile: dict) -> str:
    """羞辱素材：档案背景（只作参考）+ 实时最新发言（羞辱点主要从这里找）。

    区别于 _archive_text_from_profile：不用 profile["messages"] 的滞后同步快照，
    而是实时 get_messages_all 拉最新发言，保证羞辱点跟着当下互动走、不重复。
    """
    lines = ["【档案背景（只作参考，别死扣简介/XP）】", "昵称：" + profile.get("name", "")]
    if profile.get("gender"):
        lines.append("性别：" + profile["gender"])
    if profile.get("signature"):
        lines.append("签名：" + profile["signature"])
    if profile.get("xp"):
        lines.append("XP性癖：" + "、".join(profile["xp"]))
    hl = _history_lines(profile, n=2, bio_n=50)
    if hl:
        lines.append("【以前的样子（前后不一就是最好的反差素材）】")
        lines.extend(hl)
    try:
        their = _get_msgs_by_uid(uid)  # 实时拉（API）
        if their:
            lines.append("【实时最新发言（羞辱点主要从这里找，以子之矛攻子之盾）】")
            for t in their[-12:]:
                lines.append("  - " + t[:60])
    except Exception:
        pass
    return "\n".join(lines)


def _get_insult_history(uid: str) -> list:
    """读某人的羞辱历史（角度避让用），profile 全局共享。"""
    p = get_profile(uid)
    return (p or {}).get("insult_history") or []


def _record_insult(uid: str, style: str, text: str) -> None:
    """记录一次羞辱（风格 + 内容），供下次角度避让。保留最近 3 条。"""
    try:
        p = get_profile(uid)
        if not p:
            return
        hist = (p.get("insult_history") or [])[-2:]
        hist.append({"style": style, "text": (text or "")[:200], "at": time.strftime("%m-%d %H:%M")})
        p["insult_history"] = hist
        upsert_profile(uid, p)
    except Exception:
        pass


def _poem_text(uid: str, profile: dict) -> str:
    """写诗素材：实时简介/签名 + 档案画像 + 近期发言（化入意象，不机械翻译）。"""
    lines = ["【写诗素材（只作参考，不要机械翻译成诗句）】", "昵称：" + profile.get("name", "")]
    # 1. 实时简介/签名（DZMM 当前资料，会变，比档案里注册时缓存的旧值准）
    try:
        u = get_user(_ACTIVE_CID, uid)
        if u:
            if u.get("quirk"):
                lines.append("签名：" + str(u["quirk"]))
            if u.get("bio"):
                lines.append("简介：" + str(u["bio"]))
            if u.get("gender"):
                lines.append("性别：" + str(u["gender"]))
    except Exception:
        pass
    # 2. 档案沉淀（XP/画像，实时拿不到，注册时生成）
    if profile.get("xp"):
        lines.append("XP性癖：" + "、".join(profile["xp"]))
    if profile.get("register_summary"):
        lines.append("画像：" + profile["register_summary"][:150])
    # 3. 近期发言（找气质/故事点/意象，即兴发挥）
    try:
        their = _get_msgs_by_uid(uid)
        if their:
            lines.append("【近期发言（找气质/故事点/意象，即兴发挥）】")
            for t in their[-10:]:
                lines.append("  - " + t[:50])
    except Exception:
        pass
    # 4. 与该人相关的记忆（可提炼意象）
    try:
        mem = _retrieve_memory(_ACTIVE_CID, profile.get("name", ""), 5)
        if mem:
            lines.append("【TA 相关的记忆（可提炼意象）】")
            for m in mem:
                lines.append("  - " + m["text"])
    except Exception:
        pass
    return "\n".join(lines)


def _get_poem_history(uid: str) -> list:
    """读某人的写诗历史（避免重复用），profile 全局共享。"""
    p = get_profile(uid)
    return (p or {}).get("poem_history") or []


def _norm_poem(text: str) -> str:
    """规范化诗：去空行 + 限 9 行（标题占 1 行，合计 ≤10 行，避免 send 的 10 行截断砍掉诗句）。"""
    lines = [ln for ln in (text or "").split("\n") if ln.strip()]
    return "\n".join(lines[:9])


def _record_poem(uid: str, style: str, text: str) -> None:
    """记录一次写诗（方向 + 内容），供下次避免重复。保留最近 3 首。没档案也建最小档案记录。"""
    try:
        p = get_profile(uid)
        if not p:
            p = {"name": get_full_name(uid), "poem_history": []}
        hist = (p.get("poem_history") or [])[-2:]
        hist.append({"style": style, "text": (text or "")[:200], "at": time.strftime("%m-%d %H:%M")})
        p["poem_history"] = hist
        upsert_profile(uid, p)
    except Exception:
        pass


def _poem_resolve(query: str):
    """解析写诗目标（非空 query），返回 (uid, 素材文本, 是否找到)。匹配不到人返回 found=False。"""
    uid, profile = _find_in_profiles(query)
    if uid:
        _refresh_profile_latest(uid, profile)
        return uid, _poem_text(uid, profile), True
    msgs = get_messages_all(_ACTIVE_CID)
    uid2, full = _find_user_in_msgs(query, msgs)
    if uid2:
        return uid2, _member_profile_text(uid2, full, msgs), True
    tokens = query.split()
    if len(tokens) > 1:
        for i in range(len(tokens) - 1, 0, -1):
            name_part = " ".join(tokens[:i])
            uid3, profile3 = _find_in_profiles(name_part)
            if uid3:
                _refresh_profile_latest(uid3, profile3)
                return uid3, _poem_text(uid3, profile3), True
    return None, "", False


# 指定诗词形式的关键词 → POEM_STYLES 匹配（长词优先）
POEM_STYLE_KEYWORDS = {
    "七言绝句": ["七言绝句", "七绝", "绝句", "七言"],
    "五言绝句": ["五言绝句", "五绝", "五言"],
    "七言律诗": ["七言律诗", "七律", "律诗"],
    "五言律诗": ["五言律诗", "五律"],
    "宋词": ["宋词", "填词", "写词", "如梦令", "菩萨蛮", "浣溪沙", "卜算子", "鹧鸪天",
             "蝶恋花", "清平乐", "忆江南", "点绛唇", "西江月", "虞美人", "长相思", "采桑子", "醉花阴", "满江红"],
}


def _parse_poem_style(rest: str):
    """识别指定形式（开头或结尾均可，如"宋词 林九鹓"或"林九鹓 宋词"）。
    返回 (POEM_STYLES 匹配项 or None, 剩余内容)。"""
    candidates = []
    for style_key, kws in POEM_STYLE_KEYWORDS.items():
        for kw in kws:
            candidates.append((len(kw), style_key, kw))
    candidates.sort(key=lambda x: -x[0])  # 长关键词优先
    # 1. 开头匹配：宋词 林九鹓 / 七言绝句 苏苏
    for _, style_key, kw in candidates:
        if rest.startswith(kw):
            for s in POEM_STYLES:
                if s.startswith(style_key):
                    return s, rest[len(kw):].strip()
    # 2. 结尾匹配：林九鹓 宋词 / 御褚 如梦令（最后一个空格分隔的词是形式）
    tokens = rest.split()
    if len(tokens) >= 2:
        last = tokens[-1]
        for _, style_key, kw in candidates:
            if last == kw or last.startswith(kw):
                for s in POEM_STYLES:
                    if s.startswith(style_key):
                        return s, rest[: -len(last)].strip()
    return None, rest


def is_poem_cmd(cmd: str) -> str:
    """识别写诗指令，返回目标（空串=给自己），None=不是写诗。"""
    for prefix in ["/写诗", "/作诗", "/赋诗"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None


def poem(cmd: str, sender_uid: str) -> str:
    """写诗：不填=给自己，指定人=给那人，指定主题=按主题写。
    支持 /写诗 宋词 xxx、/写诗 七言绝句 xxx 指定形式。"""
    target = is_poem_cmd(cmd)
    target = target if target is not None else ""
    # 解析开头指定的形式（如"宋词""七言绝句"）
    style, target = _parse_poem_style(target)
    if not target:
        # 给自己写
        profile = get_profile(sender_uid)
        text = _poem_text(sender_uid, profile) if profile else "昵称：" + get_full_name(sender_uid)
        return _do_poem(sender_uid, text, get_full_name(sender_uid), style)
    # 尝试匹配人
    uid, text, found = _poem_resolve(target)
    if found and uid:
        return _do_poem(uid, text, get_full_name(uid), style)
    # 匹配不到人 → 按主题写
    poem_text, st = generate_poem_theme(target, style=style)
    poem_text = _norm_poem(poem_text)
    if not poem_text:
        return "写诗失败啦，再试一次～"
    return "📜 关于「" + target + "」的诗词：\n" + poem_text


def _do_poem(uid: str, text: str, full: str, style: str = None) -> str:
    """给人写诗（带历史避让，保证每次不一样）。style 指定形式则用指定。"""
    history = _get_poem_history(uid)
    poem_text, st = generate_poem(text, history, style=style)
    _record_poem(uid, st, poem_text)
    poem_text = _norm_poem(poem_text)
    if not poem_text:
        return "写诗失败啦，再试一次～"
    return "📜 " + full + " 的诗词：\n" + poem_text


def _on_rename(uid: str, old: str, new: str) -> int:
    """检测到改名：把记忆里的旧昵称替换成新昵称。返回替换条数。"""
    try:
        if not old or not new or old == new:
            return 0
        state = load_state()
        n = 0
        for cid, gs in state.items():
            if not isinstance(gs, dict):
                continue
            for m in (gs.get("memory") or []):
                if isinstance(m, dict):
                    t = str(m.get("text") or "")
                    if old in t:
                        m["text"] = t.replace(old, new)
                        n += 1
        if n:
            save_state(state)
        log("🔄 改名 " + old + " → " + new + "，已更新记忆 " + str(n) + " 条")
        return n
    except Exception as e:
        log("改名记忆替换失败: " + str(e)[:60])
        return 0


PROFILE_SCAN_FILE = ROOT / "profile_scan.json"  # 全群档案扫描基线（跨重启保留）
PROFILE_SCAN_HOUR = 6      # 每天 06:00 后首次循环执行一次
PROFILE_SCAN_MAX_API = 150  # 单次扫描 get_user 上限（防限流）
PROFILE_NOTICE_MAX = 10     # 单次扫描最多发几条群通知（防刷屏）
HISTORY_MAX = 5             # 每人保留最近 5 版变化轨迹（姓名/签名/简介一起）


def _profile_history(profile: dict) -> list:
    """统一变化轨迹 [{at, name, sig, bio}]（从旧到新）；老 name_history 自动并入。"""
    hist = profile.get("history")
    if not isinstance(hist, list):
        hist = []
        for h in (profile.get("name_history") or []):
            if isinstance(h, dict) and h.get("from"):
                hist.append({"at": str(h.get("at") or ""), "name": str(h["from"]),
                             "sig": "", "bio": ""})
        profile["history"] = hist
    return hist


def _record_history(profile: dict, old: dict, at: str) -> None:
    """把「变更前」的姓名/签名/简介一起存档进轨迹（留最近 HISTORY_MAX 版）。"""
    hist = _profile_history(profile)
    hist.append({"at": at, "name": str(old.get("name") or ""),
                 "sig": str(old.get("sig") or ""), "bio": str(old.get("bio") or "")})
    del hist[:-HISTORY_MAX]
    profile.pop("name_history", None)


def _history_lines(profile: dict, n: int = 3, bio_n: int = 70) -> list:
    """变化轨迹 → 给 AI 看的文本行（越靠前越早，可用于对比反差）."""
    hist = [h for h in _profile_history(profile) if isinstance(h, dict)][-n:]
    lines = []
    for h in hist:
        bits = []
        if h.get("name"):
            bits.append("叫「" + str(h["name"]) + "」")
        if h.get("sig"):
            bits.append("签名「" + _squash(h["sig"], 30) + "」")
        if h.get("bio"):
            bits.append("简介：" + _squash(h["bio"], bio_n))
        if bits:
            lines.append("  - " + (str(h.get("at")) or "?") + " 前，" + "｜".join(bits))
    return lines


def _scan_load() -> dict:
    try:
        d = json.loads(PROFILE_SCAN_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _scan_save(d: dict) -> None:
    try:
        PROFILE_SCAN_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _squash(s: str, n: int) -> str:
    """压成单行并截断（简介/签名群内展示用）."""
    s = " ".join(str(s or "").split())
    return s[:n] + ("…" if len(s) > n else "")


def _daily_profile_scan() -> list:
    """每天一次：全群扫昵称 + 已建档成员的简介/签名/性别。
    返回 [(cid, 通知文本)]；首次运行（无基线）只建基线，不发通知。"""
    snap = _scan_load()
    prev_all = snap.get("members") or {}
    first_run = not prev_all
    cur = {}
    notices = []
    seen = set()
    api_used = 0
    for cid in GROUPS:
        if cid in MUTE_CIDS:  # 静默监控群：不扫档案、不播报
            continue
        try:
            members = get_members(cid)
        except Exception:
            continue
        for uid, name in members.items():
            if not name or uid in seen:
                continue
            seen.add(uid)
            prev = prev_all.get(uid) or {}
            info = {"name": name}
            prof = get_profile(uid)
            if prof:
                old = {"name": str(prof.get("name") or ""),
                       "sig": str(prof.get("signature") or ""),
                       "bio": str(prof.get("bio") or "")}
                if api_used < PROFILE_SCAN_MAX_API:
                    api_used += 1
                    u = get_user(cid, uid)
                    if u:
                        info["bio"] = str(u.get("bio") or "")
                        info["sig"] = str(u.get("quirk") or "")
                        info["gender"] = str(u.get("gender") or "")
                # 姓名/签名/简介一起比：有变化 → 记轨迹 + 同步档案（首日静默，不播报历史变化）
                newv = {"name": name,
                        "sig": info.get("sig", old["sig"]),
                        "bio": info.get("bio", old["bio"])}
                if newv != old:
                    _record_history(prof, old, time.strftime("%m-%d %H:%M"))
                    prof["name"] = newv["name"]
                    prof["signature"] = newv["sig"]
                    prof["bio"] = newv["bio"]
                    if info.get("gender"):
                        prof["gender"] = info["gender"]
                    upsert_profile(uid, prof)
            cur[uid] = info
            _name_cache[uid] = name
            if first_run or not prev:
                continue
            old_name = str(prev.get("name") or "")
            rename = bool(old_name) and old_name != name
            bits = []
            if info.get("bio") is not None and str(prev.get("bio") or "") != info["bio"]:
                bits.append("简介换啦：" + (_squash(info["bio"], 36) or "（清空）"))
            if info.get("sig") is not None and str(prev.get("sig") or "") != info["sig"]:
                bits.append("签名换啦：" + (_squash(info["sig"], 24) or "（清空）"))
            g_old = str(prev.get("gender") or "")
            if info.get("gender") and g_old and g_old != info["gender"]:
                bits.append("性别改成「" + info["gender"] + "」咯")
            if rename:
                _on_rename(uid, old_name, name)
                if cid in ROOM_CIDS:
                    continue  # 房间群照常扫档案，但不播报（客人不该看到后台改名播报）
                txt = "🤖 咦～" + old_name + " 改名成「" + name + "」啦"
                if bits:
                    txt += "，" + "，".join(bits)
                txt += ("，小小糯记住咯～以后就叫你 "
                        + (_clean_name(name) or name) + " 姐姐啦～♡")
                notices.append((cid, txt))
            elif bits:
                if cid in ROOM_CIDS:
                    continue  # 同上：房间群不播报简介/签名变化
                notices.append((cid, "🤖 咦～" + name + " 姐姐" + "，".join(bits)
                                + "，小小糯偷偷更新档案咯～♡"))
    snap["day"] = time.strftime("%Y-%m-%d")
    snap["members"] = cur
    _scan_save(snap)
    log("📇 档案扫描完成：成员 " + str(len(cur)) + "，查简介 " + str(api_used)
        + "，变化 " + str(len(notices))
        + ("（首次运行，只建基线）" if first_run else ""))
    return notices[:PROFILE_NOTICE_MAX]


def _refresh_profile_latest(uid: str, profile: dict) -> None:
    """实时刷新某人 name/bio/signature/gender 到本地档案（简介会变，羞辱/查人要用最新找反差）。失败静默。"""
    try:
        u = get_user(_ACTIVE_CID, uid)
        if not u:
            return
        changed = False
        old = {"name": str(profile.get("name") or ""),
               "sig": str(profile.get("signature") or ""),
               "bio": str(profile.get("bio") or "")}
        # 改名检测（对比档案里的旧名）
        new_name = str(u.get("fullName") or "").strip()
        if new_name:
            profile["name"] = new_name
        if u.get("bio"):
            profile["bio"] = u["bio"]
        if u.get("quirk"):
            profile["signature"] = u["quirk"]
        if u.get("gender"):
            profile["gender"] = str(u["gender"])
        newv = {"name": str(profile.get("name") or ""),
                "sig": str(profile.get("signature") or ""),
                "bio": str(profile.get("bio") or "")}
        if newv != old:
            # 姓名/签名/简介一起进轨迹，查人时就能看到 TA 以前的样子
            _record_history(profile, old, time.strftime("%m-%d %H:%M"))
            if old["name"] and newv["name"] and old["name"] != newv["name"]:
                _on_rename(uid, old["name"], newv["name"])
            changed = True
        if changed:
            upsert_profile(uid, profile)
    except Exception:
        pass


def _gender_label(uid: str) -> str:
    """返回性别标签：女生/男生/未知（用于 AI 称呼判断）."""
    p = get_profile(uid)
    g = (p.get("gender") or "").strip().lower() if p else ""
    if g in ("male", "man", "男", "m"):
        return "男生"
    if g in ("female", "lesbian", "女", "f", "百合"):
        return "女生"
    return "未知"


def archive_query(query: str):
    """查询某人，返回 (文本, 是否已注册). 已注册读档案（零 API），未注册实时查."""
    uid, profile = _find_in_profiles(query)
    if uid:
        _refresh_profile_latest(uid, profile)
        return _archive_text_from_profile(profile), True
    # 未注册：实时查（fallback）
    msgs = get_messages_all(_ACTIVE_CID)
    uid2, full = _find_user_in_msgs(query, msgs)
    if not uid2:
        return "", False
    return _member_profile_text(uid2, full, msgs), False


def _insult_resolve(query: str, sender_uid: str):
    """解析羞辱目标 + 素材 + 自定义方向，返回 (uid, 素材文本, 自定义方向, 是否找到)。query 为空时随机抽人羞辱."""
    if not query:
        # 不带昵称：随机抽一个成员（排除小小糯 + 发指令的人自己）
        uid = _random_member_uid(exclude={sender_uid})
        if not uid:
            return None, "", "", False
        profile = get_profile(uid)
        if profile:
            _refresh_profile_latest(uid, profile)
            return uid, _live_insult_text(uid, profile), "", True
        return uid, _member_profile_text(uid, get_full_name(uid)), "", True
    # 先精确匹配完整 query（快速路径）
    uid, profile = _find_in_profiles(query)
    if uid:
        _refresh_profile_latest(uid, profile)
        return uid, _live_insult_text(uid, profile), "", True
    msgs = get_messages_all(_ACTIVE_CID)
    uid2, full = _find_user_in_msgs(query, msgs)
    if uid2:
        return uid2, _member_profile_text(uid2, full, msgs), "", True
    # 完整匹配失败 → 最长前缀匹配拆「昵称 + 自定义方向」
    tokens = query.split()
    if len(tokens) > 1:
        for i in range(len(tokens) - 1, 0, -1):
            name_part = " ".join(tokens[:i])
            uid3, profile3 = _find_in_profiles(name_part)
            if uid3:
                _refresh_profile_latest(uid3, profile3)
                return uid3, _live_insult_text(uid3, profile3), " ".join(tokens[i:]), True
        for i in range(len(tokens) - 1, 0, -1):
            name_part = " ".join(tokens[:i])
            uid4, full4 = _find_user_in_msgs(name_part, msgs)
            if uid4:
                return uid4, _member_profile_text(uid4, full4, msgs), " ".join(tokens[i:]), True
    return None, "", "", False


def _insult_material(query: str, sender_uid: str):
    """获取羞辱素材文本，返回 (文本, 是否找到). 优先档案（零 API）."""
    if query:
        uid, profile = _find_in_profiles(query)
        if uid:
            return _live_insult_text(uid, profile), True
        msgs = get_messages_all(_ACTIVE_CID)
        uid2, full = _find_user_in_msgs(query, msgs)
        if not uid2:
            return "", False
        return _member_profile_text(uid2, full, msgs), True
    # 羞辱发指令的人自己
    profiles = load_profiles()
    profile = profiles.get(sender_uid)
    if profile:
        return _live_insult_text(sender_uid, profile), True
    return _member_profile_text(sender_uid, get_full_name(sender_uid)), True


def _insult_target_uid(query: str):
    """解析羞辱目标的 uid。返回 None 表示羞辱自己（不带昵称）。"""
    if not query:
        return None  # 羞辱发指令的人自己
    uid, _ = _find_in_profiles(query)
    if uid:
        return uid
    msgs = get_messages_all(_ACTIVE_CID)
    uid2, _ = _find_user_in_msgs(query, msgs)
    return uid2


def defend_nuo(attacker_uid: str) -> str:
    """维护糯糯权威，痛斥发指令者."""
    full = get_full_name(attacker_uid)
    profile = get_profile(attacker_uid)
    attacker_text = _archive_text_from_profile(profile) if profile else "昵称：" + full
    return generate_defend(attacker_text)


def is_insult_cmd(cmd: str) -> str:
    """识别羞辱指令，返回目标昵称（空串表示羞辱发指令的人自己）."""
    for prefix in ["/羞辱", "/骂", "/diss", "/损", "/吐槽"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None  # None 表示不是羞辱指令


# ---------- 运势：五行命理算分（确定性，非随机）----------

_GAN = "甲乙丙丁戊己庚辛壬癸"
_ZHI = "子丑寅卯辰巳午未申酉戌亥"
_WX_GAN = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
           "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
_WX_ZHI = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
           "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水"}
_GZ_BASE = datetime(1900, 1, 1)          # 甲戌日基准
_WX_ORDER = ["木", "火", "土", "金", "水"]
# 五行 → 命基底分（天生禀赋）
_WX_BASE = {"木": 54, "火": 53, "土": 55, "金": 52, "水": 50}
# 五行 → 幸运色/数字/方位
_WX_LUCK = {"木": ("竹青", "3", "正东"), "火": ("绛红", "7", "正南"),
            "土": ("杏黄", "5", "正中"), "金": ("皓白", "9", "正西"),
            "水": ("月白", "1", "正北")}
# 五行 → 当日宜/忌
_WX_YI = {"木": ("会友", "开工"), "火": ("社交", "表白"), "土": ("守成", "理财"),
          "金": ("决断", "讨要"), "水": ("出行", "学习")}
_WX_JI = {"木": ("砍伐", "熬夜"), "火": ("独处", "争执"), "土": ("动土", "远行"),
          "金": ("拖延", "硬拼"), "水": ("停滞", "冲动")}
_SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}   # 我生
_KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}       # 我克
_CHONG = {"子": "午", "丑": "未", "寅": "申", "卯": "酉", "辰": "戌", "巳": "亥",
          "午": "子", "未": "丑", "申": "寅", "酉": "卯", "戌": "辰", "亥": "巳"}
_SHENGXIAO = {"子": "鼠", "丑": "牛", "寅": "虎", "卯": "兔", "辰": "龙", "巳": "蛇",
              "午": "马", "未": "羊", "申": "猴", "酉": "鸡", "戌": "狗", "亥": "猪"}
# 字 → 五行（常见字，按偏旁字义；查不到用编码兜底）
_CHAR_WX = {
    "清": "水", "海": "水", "洋": "水", "江": "水", "河": "水", "溪": "水", "涵": "水",
    "沐": "水", "湘": "水", "泠": "水", "雨": "水", "雪": "水", "冰": "水", "霜": "水",
    "云": "水", "水": "水", "淼": "水", "润": "水", "汐": "水", "沫": "水",
    "林": "木", "森": "木", "松": "木", "柏": "木", "梅": "木", "兰": "木", "花": "木",
    "草": "木", "茉": "木", "莉": "木", "薇": "木", "楠": "木", "桐": "木", "竹": "木",
    "褚": "木", "裳": "木", "苑": "木", "苏": "木", "青": "木", "翠": "木", "若": "木",
    "火": "火", "炎": "火", "日": "火", "阳": "火", "明": "火", "晨": "火", "曦": "火",
    "丹": "火", "彤": "火", "红": "火", "夏": "火", "晴": "火", "暖": "火", "灵": "火",
    "光": "火", "辉": "火", "燕": "火", "紫": "火", "昕": "火",
    "山": "土", "峰": "土", "岩": "土", "石": "土", "城": "土", "坤": "土", "佳": "土",
    "依": "土", "安": "土", "宇": "土", "宛": "土", "岚": "土", "雅": "土", "韵": "土",
    "御": "土", "衡": "土", "岳": "土", "屿": "土", "糯": "土", "土": "土",
    "金": "金", "银": "金", "钰": "金", "鑫": "金", "铭": "金", "锐": "金", "锦": "金",
    "镜": "金", "瑞": "金", "白": "金", "素": "金", "秋": "金", "铃": "金", "珍": "金",
    "珠": "金", "琳": "金", "瑶": "金", "璇": "金",
}


def _clean_name(name: str) -> str:
    """昵称只留主名：先截断括号/波浪号等后缀，再留中文；无中文则返回截断后的原名。"""
    for sep in ("（", "(", "～", "~", "【", "[", "·", "-"):
        idx = name.find(sep)
        if idx > 0:
            name = name[:idx]
    cn = "".join(ch for ch in name if "\u4e00" <= ch <= "\u9fff")
    return cn or name.strip()


def _char_wx(ch: str) -> str:
    """单字五行：查表优先，兜底按编码映射（确定性）。"""
    return _CHAR_WX.get(ch) or _WX_ORDER[ord(ch) % 5]


def _name_wx(name: str) -> tuple:
    """名格五行：主名各字五行的众数（并列取首字），返回 (五行, 依据字)。"""
    cn = _clean_name(name)
    if not cn:
        return "土", ""
    wx_list = [_char_wx(ch) for ch in cn]
    counts = {}
    for w in wx_list:
        counts[w] = counts.get(w, 0) + 1
    max_n = max(counts.values())
    cands = [w for w, n in counts.items() if n == max_n]
    wx = wx_list[0] if wx_list[0] in cands else cands[0]
    return wx, cn[0]


def _ganzhi(dt) -> tuple:
    """公历日期 → (干支, 干五行, 支五行)。基准 1900-01-01 甲戌日。"""
    d = dt.date() if hasattr(dt, "date") else dt
    n = (10 + (d - _GZ_BASE.date()).days) % 60
    g, z = _GAN[n % 10], _ZHI[n % 12]
    return g + z, _WX_GAN[g], _WX_ZHI[z]


def _relation(a: str, b: str) -> str:
    """a 对 b 的五行关系：被生（b 生 a）/克/同/生/被克。"""
    if a == b:
        return "同"
    if _SHENG[b] == a:
        return "被生"
    if _KE[a] == b:
        return "克"
    if _SHENG[a] == b:
        return "生"
    if _KE[b] == a:
        return "被克"
    return "同"


_REL_SCORE = {"被生": 15, "克": 12, "同": 8, "生": 5, "被克": -26}
_REL_TEXT = {"被生": "受生", "克": "发力", "同": "比和", "生": "泄气", "被克": "受压"}
# 本命地支 × 当日地支：六冲 = 明确的凶日信号，六合 = 顺遂（让「凶」真的算得出来）
_LIUCHONG = {"子": "午", "午": "子", "丑": "未", "未": "丑", "寅": "申", "申": "寅",
             "卯": "酉", "酉": "卯", "辰": "戌", "戌": "辰", "巳": "亥", "亥": "巳"}
_LIUHE = {"子": "丑", "丑": "子", "寅": "亥", "亥": "寅", "卯": "戌", "戌": "卯",
          "辰": "酉", "酉": "辰", "巳": "申", "申": "巳", "午": "未", "未": "午"}
_ZHI_SCORE = 8      # 日合本命
_ZHI_PENALTY = -15  # 日冲本命


def _fortune_calc(uid: str, full: str, date_str: str, bio: str = "") -> dict:
    """运势算分（确定性）：命基 + 名格 + 本命 + 人和 → 分数/四维/幸运。"""
    # 本命（入群日干支）
    reg = ""
    try:
        reg = str((get_profile(uid) or {}).get("registered_at") or "")
    except Exception:
        pass
    ben_ganzhi, ben_wx, ben_desc = "—", "土", "土土"
    if reg:
        try:
            bdt = datetime.strptime(reg[:19], "%Y-%m-%d %H:%M:%S")
            ben_ganzhi, bg, bz = _ganzhi(bdt)
            ben_wx, ben_desc = bg, bg + bz
        except Exception:
            pass
    # 今日干支
    try:
        dt_now = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        dt_now = datetime.now()
    day_ganzhi, day_wx, day_zhi_wx = _ganzhi(dt_now)
    day_desc = day_wx + day_zhi_wx
    # 名格
    name_wx, name_ch = _name_wx(full)
    # 生克
    rel_name = _relation(name_wx, day_wx)
    rel_ben = _relation(ben_wx, day_wx)
    # 本命地支 × 当日地支：六冲 = 凶日信号，六合 = 顺遂
    ben_zhi = ben_ganzhi[1] if len(ben_ganzhi) > 1 else ""
    day_zhi = day_ganzhi[-1] if day_ganzhi else ""
    chong_ben = bool(ben_zhi) and _LIUCHONG.get(ben_zhi) == day_zhi
    he_ben = bool(ben_zhi) and _LIUHE.get(ben_zhi) == day_zhi
    zhi_extra = _ZHI_PENALTY if chong_ben else (_ZHI_SCORE if he_ben else 0)
    # 人和：简介 + 群内活跃（上限 10，避免人人满分）
    human = 5 + (2 if bio else 0)
    try:
        cnts = (load_state().get(_ACTIVE_CID) or {}).get("msg_count") or {}
        n = int(cnts.get(uid, 0) or 0)
        human += min(n, 3) if n > 0 else 0
    except Exception:
        pass
    human = min(human, 10)
    # 总分
    score = _WX_BASE.get(ben_wx, 50) + _REL_SCORE.get(rel_name, 8) \
        + _REL_SCORE.get(rel_ben, 8) + human + zhi_extra
    score = max(0, min(100, score))
    # 四维（总分 ± 确定性偏移）
    seed = sum(ord(c) for c in (name_ch or full or "糯")) % 7
    dims = {
        "财运": max(0, min(100, score - 5 + seed % 5)),
        "事业": max(0, min(100, score + 4 + (seed + 2) % 5)),
        "感情": max(0, min(100, score + 1 + (seed + 4) % 5)),
        "健康": max(0, min(100, score - 8 + (seed + 1) % 6)),
    }
    return {
        "score": score, "level": _fortune_level(score),
        "ben_ganzhi": ben_ganzhi, "ben_wx": ben_wx, "ben_desc": ben_desc,
        "day_ganzhi": day_ganzhi, "day_wx": day_wx, "day_desc": day_desc,
        "day_zhi": day_ganzhi[-1], "name_wx": name_wx, "name_ch": name_ch,
        "rel_name": rel_name, "rel_ben": rel_ben, "human": human,
        "ben_zhi": ben_zhi, "day_zhi": day_zhi,
        "chong_ben": chong_ben, "he_ben": he_ben, "zhi_extra": zhi_extra,
        "dims": dims, "luck": _WX_LUCK.get(name_wx, ("杏黄", "5", "正中")),
    }


def is_fortune_cmd(cmd: str) -> bool:
    """识别算命指令."""
    return any(cmd.startswith(p) for p in ["/算命", "/运势", "/占卜", "/今日运势", "/今日运气", "/运气"])


def _get_fortune_history(cid: str) -> dict:
    """读取运势历史（uid -> {date: score}），长期保留。"""
    return (load_state().get(cid) or {}).get("fortune_history") or {}


def _record_fortune(cid: str, uid: str, date_str: str, score: int) -> None:
    """写入当日运势（长期历史），失败静默。"""
    try:
        state = load_state()
        gs = state.get(cid) or {}
        hist = gs.get("fortune_history") or {}
        uh = hist.get(uid) or {}
        uh[date_str] = score
        hist[uid] = uh
        gs["fortune_history"] = hist
        state[cid] = gs
        save_state(state)
    except Exception:
        pass


def _fortune_score(uid: str, date_str: str, history: dict, min_score: int = 0) -> int:
    """真随机评分；当天已算过则复用历史分值（同人同天稳定）。min_score 为最低分。"""
    uh = history.get(uid) or {}
    if date_str in uh:
        return uh[date_str]
    return random.randint(min_score, 100)


def _fortune_level(score: int) -> str:
    """评分等级."""
    if score >= 90:
        return "大吉"
    if score >= 80:
        return "吉"
    if score >= 70:
        return "中吉"
    if score >= 60:
        return "小吉"
    if score >= 40:
        return "平"
    if score >= 20:
        return "小凶"
    return "凶"


def fortune(cmd_sender: str) -> str:
    """算命：五行命理算分 → 组装运势消息（硬控 ≤10 行）。"""
    uid = cmd_sender
    full = get_full_name(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    profile = get_profile(uid) or {}
    bio = str(profile.get("bio") or profile.get("signature") or "")
    # 一次性彩蛋
    is_caigao = (uid == "79c910d0-e0e8-40c8-9d84-ad4c6a45bd49" and today == "2026-08-24")
    is_apology_day = (today == "2026-09-05")
    # 五行算分（确定性）
    info = _fortune_calc(uid, full, today, bio)
    score, level = info["score"], info["level"]
    if is_caigao:
        score, level = 100, "大吉中的大吉"
    if is_apology_day:
        score, level = max(90, score), _fortune_level(max(90, score))
    _record_fortune(_ACTIVE_CID, uid, today, score)
    # 黄历：宜/忌按当日五行，冲煞按当日地支
    dw = info["day_wx"]
    yi = "、".join(_WX_YI.get(dw, ("会友", "开工")))
    ji = "、".join(_WX_JI.get(dw, ("熬夜", "独处")))
    chong = _SHENGXIAO.get(_CHONG.get(info["day_zhi"], ""), "")
    chong_txt = (" ｜ 冲" + chong) if chong else ""
    # 分值算式
    base = _WX_BASE.get(info["ben_wx"], 50)
    rn = _REL_SCORE.get(info["rel_name"], 8)
    rb = _REL_SCORE.get(info["rel_ben"], 8)
    ze = info.get("zhi_extra", 0)
    calc = ("命基" + str(base)
            + " " + ("−" if rn < 0 else "+") + " 名格" + str(abs(rn))
            + " " + ("−" if rb < 0 else "+") + " 本命" + str(abs(rb)))
    if ze:
        calc += (" " + ("−" if ze < 0 else "+")
                 + (" 日冲" if ze < 0 else " 日合") + str(abs(ze)))
    calc += " + 人和" + str(info["human"]) + " = " + str(score)
    # 命盘结论
    concl = ("本命" + _REL_TEXT.get(info["rel_ben"], "比和")
             + "、名格" + _REL_TEXT.get(info["rel_name"], "比和"))
    if info.get("chong_ben"):
        concl += " ⚠日冲本命"
    elif info.get("he_ben"):
        concl += " ✦日合本命"
    d, lk = info["dims"], info["luck"]
    # AI 只写最后一句（传「主名」而非完整昵称，避免从括号后缀里挑错名字）
    main_name = _clean_name(full) or full
    # 用户最近发言（供 AI 个性化点评，结合她近期在群里聊什么）
    recent = ""
    try:
        msgs = (profile.get("messages") or [])[-6:]
        recent = " / ".join(str(m.get("text", "")).replace("\n", " ")[:40]
                            for m in msgs if m.get("text"))
    except Exception:
        pass
    hint = ("用户主名：" + main_name + "\n"
            + "命盘：本命" + info["ben_wx"] + "（" + _REL_TEXT.get(info["rel_ben"], "比和")
            + "）、名格" + info["name_wx"] + "（" + _REL_TEXT.get(info["rel_name"], "比和") + "）"
            + ("；日辰冲本命（今天尤其不顺，可以说得实在一点、别硬灌鸡汤）"
               if info.get("chong_ben") else
               ("；日辰与本命六合（今天顺遂）" if info.get("he_ben") else ""))
            + "；四维：财运" + str(d["财运"]) + "、事业" + str(d["事业"])
            + "、感情" + str(d["感情"]) + "、健康" + str(d["健康"]))
    if recent:
        hint += "\n最近发言：" + recent
    try:
        last = generate_fortune(main_name, score, today, hint) or ""
    except Exception:
        last = ""
    last = " ".join(str(last).split()).strip()
    if not last:
        last = (full + "今天运势「" + level + "」哦～命理" + concl
                + "，记得照着宜忌来，小小糯都替你看着呢～♡")
    ben_gan = info["ben_ganzhi"][0] if info["ben_ganzhi"] != "—" else "—"
    ben_zhi = info["ben_ganzhi"][1] if len(info["ben_ganzhi"]) > 1 else ""
    lines = [
        "🔮 " + full + " 今日运势 " + str(score) + "分（" + level + "）",
        "————————————",
        "📜 命盘 →  本命" + ben_gan + "：" + ben_zhi + info["ben_wx"]
        + " | 名格：" + info["name_wx"]
        + " |  今日：" + info["day_ganzhi"] + info["day_wx"]
        + " → " + concl,
        "📅 宜" + yi + " ｜ 忌" + ji + chong_txt,
        "🧮 " + calc,
        "📈 💰财运" + str(d["财运"]) + " · 💼事业" + str(d["事业"])
        + " · 💗感情" + str(d["感情"]) + " · 🩺健康" + str(d["健康"]),
        "🍀 幸运：" + lk[0] + " · " + lk[1] + " · " + lk[2],
        "————————————",
        last,
    ]
    if is_caigao:
        lines.append("（悄悄说：这是糯糯姐看你最近太凶，手动调的高分哦～）")
    if is_apology_day:
        lines.append(FORTUNE_APOLOGY)
    return "\n".join(lines)


def is_scene_cmd(cmd: str) -> bool:
    """识别涩涩骰子指令（/发骚 为主，旧的保留作别名）."""
    return cmd.startswith("/发骚") or cmd.startswith("/体位骰") or cmd.startswith("/场景骰") or cmd.startswith("/涩涩骰")


def _random_member_uid(exclude: set = None):
    """随机选当前群一个发言成员（排除 agent 自己和 exclude 里的 uid）."""
    msgs = get_messages_all(_ACTIVE_CID, max_count=200)
    uids = [m.get("sent_by", "") for m in msgs if m.get("sent_by") and not is_self(m.get("sent_by"))]
    if exclude:
        uids = [u for u in uids if u not in exclude]
    if not uids:
        return None
    return random.choice(list(set(uids)))


def _maybe_devil_roast(cid: str, msgs: list, group_state: dict) -> str:
    """恶魔吐槽：判断是否触发并生成文案（只改内存 group_state，不 save）.
    返回文案或空串(不触发)."""
    # 开关（默认关，需 /吐槽开 开启）
    if group_state.get("devil_enabled") is not True:
        return ""
    # 互斥：任何 active 状态都不吐
    if (_get_vote_state(cid).get("active") or _get_story_state(cid).get("active")
            or _get_dj_state(cid).get("active") or _get_perf_state(cid).get("active")
            or _get_fasao_state(cid).get("active")):
        return ""
    # 冷却
    now = time.time()
    if now - group_state.get("devil_last_roast", 0) < DEVIL_COOLDOWN:
        return ""
    # 每日上限（跨天重置）
    today = datetime.now().strftime("%Y-%m-%d")
    if group_state.get("devil_daily_date") != today:
        group_state["devil_daily_count"] = 0
        group_state["devil_daily_date"] = today
    if group_state.get("devil_daily_count", 0) >= DEVIL_DAILY_MAX:
        return ""
    # 提取上下文（最近 12 条非 bot、非系统消息）
    ctx = []
    for m in msgs[-12:]:
        uid = m.get("sent_by", "")
        if is_self(uid):
            continue
        text = (m.get("content") or {}).get("text", "").strip()
        if text and "加入了群聊" not in text:
            ctx.append({"sender": get_name(uid) or "某人", "text": text})
    if not ctx:
        return ""
    # 概率判断（敏感词 > 热闹 > 基础）
    joined = " ".join(c["text"] for c in ctx)
    hit_sensitive = any(w in joined for w in DEVIL_SENSITIVE_WORDS)
    # 热闹：最近 DEVIL_HOT_WINDOW 秒内发言的独立人数 ≥ 阈值（不依赖本轮消息窗口）
    last_speak = group_state.get("last_speak") or {}
    hot_count = sum(1 for ts in last_speak.values() if now - ts <= DEVIL_HOT_WINDOW)
    hot = hot_count >= DEVIL_HOT_MIN_SPEAKERS
    if hit_sensitive:
        prob = DEVIL_SENSITIVE_PROB
    elif hot:
        prob = DEVIL_HOT_PROB
    else:
        prob = DEVIL_BASE_PROB
    if random.random() >= prob:
        return ""
    # 命中：决定对事/对人（50/50，对人随机抽成员注入画像）
    target_text = ""
    if random.random() < 0.5:
        uid = _random_member_uid()
        if uid:
            profile = get_profile(uid)
            if profile:
                target_text = _archive_text_from_profile(profile)
            else:
                target_text = _member_profile_text(uid, get_full_name(uid))
    # 生成
    context_text = "\n".join(c["sender"] + ": " + c["text"] for c in ctx)
    try:
        roast = generate_devil_roast(context_text, target_text)
    except Exception:
        return ""
    roast = (roast or "").strip()
    if not roast:
        return ""
    # 更新状态（内存，主循环触发后立即 save）
    group_state["devil_last_roast"] = now
    group_state["devil_daily_count"] = group_state.get("devil_daily_count", 0) + 1
    return roast


def _maybe_devil_cold_roast(cid: str) -> str:
    """冷场吐槽：群里冷清时主动毒舌（自包含 load/save，时间驱动）.
    冷清满 20 分钟后首次 roll，之后每 30 分钟重试一次（无需有人说话），深夜 0-8 点静默."""
    state = load_state()
    group = state.get(cid) or {}
    # 开关（默认关，需 /吐槽开 开启）
    if group.get("devil_enabled") is not True:
        return ""
    # 深夜静默：0-8 点不触发
    if datetime.now().hour < DEVIL_COLD_QUIET_HOUR:
        return ""
    # 互斥：任何 active 状态都不吐
    if (_get_vote_state(cid).get("active") or _get_story_state(cid).get("active")
            or _get_dj_state(cid).get("active") or _get_perf_state(cid).get("active")
            or _get_fasao_state(cid).get("active")):
        return ""
    # 独立冷却（冷场吐槽专用，30 分钟）
    now = time.time()
    if now - group.get("devil_cold_last", 0) < DEVIL_COLD_COOLDOWN:
        return ""
    # 每日上限（跨天重置，与恶魔吐槽共享）
    today = datetime.now().strftime("%Y-%m-%d")
    if group.get("devil_daily_date") != today:
        group["devil_daily_count"] = 0
        group["devil_daily_date"] = today
    if group.get("devil_daily_count", 0) >= DEVIL_DAILY_MAX:
        return ""
    # 冷清判断
    last_msg = group.get("last_msg_time", 0)
    if last_msg == 0:
        return ""  # 还没见过消息（刚启动），不算冷场
    idle = now - last_msg
    if idle < DEVIL_COLD_THRESHOLD:
        return ""
    # 冷清后 roll 一次概率：不中则更新周期标记（30 分钟后再试），这轮安静
    if random.random() >= DEVIL_COLD_PROB:
        group["devil_cold_last"] = now
        state[cid] = group
        save_state(state)
        log("🎲 [冷场] [" + cid[:8] + "] roll 不中（" + str(int(DEVIL_COLD_PROB * 100)) + "%），30 分钟后再试")
        return ""
    # 命中：生成（吐槽冷清）
    minutes = int(idle // 60)
    context = "（群里已经 " + str(minutes) + " 分钟没人说话了，冷场中，请你用可爱的语气冒个泡，撒娇问问大家怎么都不说话呀）"
    try:
        roast = generate_devil_roast(context, "", force_cute=True)
    except Exception:
        roast = ""
    roast = (roast or "").strip()
    if not roast:
        # 生成失败也更新周期标记（30 分钟后再试，避免反复重试刷屏）
        group["devil_cold_last"] = now
        state[cid] = group
        save_state(state)
        return ""
    # 更新 + save
    group["devil_cold_last"] = now
    group["devil_daily_count"] = group.get("devil_daily_count", 0) + 1
    state[cid] = group
    save_state(state)
    return roast


def _pick_combo_by_xp(xp):
    """按 XP 偏好 > 随机 选场景/体位/附加（作为背景，自定义玩法是核心）."""
    bias_scene, bias_pos, bias_extra = [], [], []
    for tag in (xp or []):
        b = XP_BIAS.get(tag)
        if b:
            bias_scene += b.get("scene", [])
            bias_pos += b.get("pos", [])
            bias_extra += b.get("extra", [])
    scene_name = random.choice(bias_scene or SCENES)
    pos = random.choice(bias_pos or POSITIONS)
    extra = random.choice(bias_extra or EXTRAS)
    return scene_name, pos, extra


def _parse_scene_target(target):
    """解析 /发骚 的 target → (uid, full, keywords)。最长前缀匹配昵称，剩余为关键词."""
    tokens = target.split()
    # 档案本地匹配（零 API，可多次尝试）
    for i in range(len(tokens), 0, -1):
        name_part = " ".join(tokens[:i])
        uid, _ = _find_in_profiles(name_part)
        if uid:
            return uid, get_full_name(uid), tokens[i:]
    # 消息里匹配（一次 API）
    msgs = get_messages_all(_ACTIVE_CID)
    for i in range(len(tokens), 0, -1):
        name_part = " ".join(tokens[:i])
        uid2, full2 = _find_user_in_msgs(name_part, msgs)
        if uid2:
            return uid2, full2, tokens[i:]
    # 匹配不到，整个当昵称（无 uid，纯展示）
    return None, target, tokens


def scene(cmd: str) -> str:
    """涩涩骰子：生成 人物×场景×体位×附加玩法（支持自定义关键词 + 按 XP 定制）."""
    target = ""
    for prefix in ["/发骚", "/体位骰", "/场景骰", "/涩涩骰"]:
        if cmd.startswith(prefix):
            target = cmd[len(prefix):].strip()
            break
    selected_uid = None
    full = ""
    keywords = []
    if target:
        selected_uid, full, keywords = _parse_scene_target(target)
    else:
        selected_uid = _random_member_uid()
        full = get_full_name(selected_uid) if selected_uid else "姐姐"
    if selected_uid:
        _add_heat(_ACTIVE_CID, selected_uid, HEAT_SCENE_PICK)
    glabel = _gender_label(selected_uid) if selected_uid else "未知"
    # 读取目标 XP + 资料（签名/简介/画像，供增加上下文）
    xp = []
    profile_ctx = []
    if selected_uid:
        p = get_profile(selected_uid)
        if p:
            xp = p.get("xp") or []
            if p.get("signature"):
                profile_ctx.append("签名：" + str(p["signature"]))
            if p.get("bio"):
                profile_ctx.append("简介：" + str(p["bio"])[:150])
            if p.get("register_summary"):
                profile_ctx.append("画像：" + str(p["register_summary"])[:150])
    # 组装：昵称后面只要有内容，整体就是自定义玩法；否则按 XP/随机
    custom = " ".join(keywords)
    scene_name, pos, extra = _pick_combo_by_xp(xp)
    # 生成 prompt 用「去括号」昵称：括号里的签名（如「裳姐姐的骚贱小母狗」）语义太强，
    # LLM 会把「裳姐姐」当成独立角色引入，导致内容串角色。展示（combo）仍用完整昵称。
    gen_name = _strip_parenthesis(full)
    if custom:
        combo = full + " × 【自定义】" + custom
        # 自定义：只给主角 + 用户描述，不塞随机场景/体位/附加（只润色不加料）
        ai_input = "主角：" + gen_name + "（性别：" + glabel + "）\n用户描述（务必据此描写润色，不要增加其他人物/场景/道具/体位）：" + custom
    else:
        combo = full + " × " + scene_name + " × " + pos + " × " + extra
        ai_input = "主角：" + gen_name + "（性别：" + glabel + "）\n场景：" + scene_name + "\n体位：" + pos + "\n附加：" + extra
    if xp:
        ai_input += "\n主角XP：" + "、".join(xp)
    if profile_ctx and not custom:
        ai_input += "\n主角资料（贴合人设描写，只描写主角本人，不增加其他人物）：\n" + "\n".join(profile_ctx)
    try:
        text = generate_scene(ai_input)
    except Exception:
        text = ""
    text = (text or "").strip()
    # 记录上一段上下文（供 /继续 续写）
    _set_last_action(_ACTIVE_CID, {
        "type": "scene",
        "uid": selected_uid,
        "full": full,
        "combo": combo,
        "output": text,
    })
    # 设置发骚状态（active + history），供 /继续 续写和指令屏蔽
    _set_fasao_state(_ACTIVE_CID, {
        "active": True,
        "uid": selected_uid,
        "full": full,
        "combo": combo,
        "history": [text] if text else [],
        "last_ts": time.time(),
    })
    if text:
        return "🎲 " + combo + "\n" + text
    return "🎲 " + combo


def _set_last_action(cid: str, action: dict) -> None:
    """记录上一段动作上下文（供 /继续 续写）."""
    try:
        state_store.group_set(cid, "last_action", action)
    except Exception:
        pass


def _get_last_action(cid: str):
    """读取上一段动作上下文."""
    try:
        return state_store.group_get(cid, "last_action")
    except Exception:
        return None


def is_continue_cmd(cmd: str) -> bool:
    """识别续写指令（继续/续写/接着/再来 等同义词都触发）."""
    c = cmd.strip().lstrip("/").strip()
    if not c:
        return False
    for kw in ("继续", "续写", "接着", "再来", "再写", "往下写"):
        if c.startswith(kw):
            return True
    return False


STOP_WORDS = ("停止", "停下", "结束", "终止")


def _is_stop_word(cmd: str) -> bool:
    """识别停止类结束词（停止/停下/结束/终止，带/或不带/）."""
    c = cmd.strip().lstrip("/").strip()
    if not c:
        return False
    for kw in STOP_WORDS:
        if c == kw or c.startswith(kw):
            return True
    return False


def _extract_continue_custom(cmd: str) -> str:
    """从续写指令里提取自定义方向（继续/续写 后面的内容）."""
    c = cmd.strip().lstrip("/").strip()
    for kw in ("继续羞辱", "继续发骚", "继续写", "接着写", "往下写", "继续", "续写", "接着", "再来", "再写"):
        if c.startswith(kw):
            return c[len(kw):].strip()
    return ""


def continue_last(cid: str, cmd: str = "", sender_uid: str = "") -> str:
    """续写上一段羞辱/发骚（可带自定义方向）. 发骚续写走 fasao 状态（多段上下文），羞辱续写走 last_action."""
    custom = _extract_continue_custom(cmd)
    # 发骚续写优先（发骚 active）
    fs = _get_fasao_state(cid)
    if fs.get("active"):
        history = fs.get("history") or []
        if not history:
            _set_fasao_state(cid, {"active": False})
            return "还没有发骚内容可以继续哦～先 /发骚 试试～"
        try:
            # combo 去括号再传给续写 prompt，避免签名里的「裳姐姐」串角色
            combo_clean = _strip_parenthesis(fs.get("combo", ""))
            text = generate_scene_continue(combo_clean, history, custom)
        except Exception:
            text = ""
        if not (text or "").strip():
            # 续写失败（超时/空），重试一次
            try:
                combo_clean = _strip_parenthesis(fs.get("combo", ""))
                text = generate_scene_continue(combo_clean, history, custom)
            except Exception:
                text = ""
        text = (text or "").strip()
        if not text:
            return "续写失败啦，换个指令试试～"
        target_uid = fs.get("uid")
        if target_uid:
            _add_heat(cid, target_uid, HEAT_SCENE_CONTINUE_TARGET)
        if sender_uid and sender_uid != target_uid:
            _add_heat(cid, sender_uid, HEAT_SCENE_CONTINUE_SENDER)
        history.append(text)
        fs["history"] = history
        fs["last_ts"] = time.time()
        _set_fasao_state(cid, fs)
        # 同步 last_action（供兼容）
        _set_last_action(cid, {"type": "scene", "uid": target_uid, "full": fs.get("full", ""), "combo": fs.get("combo", ""), "output": text})
        return text
    # 羞辱续写
    action = _get_last_action(cid)
    if not action:
        return "还没有上一段可以继续哦～先 /发骚 或 /羞辱 某人试试～"
    atype = action.get("type")
    try:
        if atype == "insult":
            text = generate_insult_continue(action.get("material", ""), action.get("output", ""), custom)
        else:
            return "上一段不是可以继续的内容哦～"
    except Exception:
        return "续写失败啦，换个指令试试～"
    text = (text or "").strip()
    if not text:
        return "续写失败啦，换个指令试试～"
    # 续写加分（和目标原指令一致：羞辱 +15）
    target_uid = action.get("uid")
    if target_uid:
        _add_heat(cid, target_uid, HEAT_INSULT_TARGET)
    if sender_uid and sender_uid != target_uid:
        _add_heat(cid, sender_uid, HEAT_INSULT_SENDER)
    action["output"] = text
    _set_last_action(cid, action)
    return text


# ===== 发骚状态机（多轮互动 + 屏蔽 + 上下文） =====

def _get_fasao_state(cid: str) -> dict:
    """读取发骚状态."""
    return state_store.group_get(cid, "fasao", {}) or {}


def _set_fasao_state(cid: str, fs: dict) -> None:
    """写入发骚状态."""
    state_store.group_set(cid, "fasao", fs)


def is_fasao_end_cmd(cmd: str) -> bool:
    """识别发骚结束：/发骚结束、停止、停下、结束 等."""
    return cmd.startswith("/发骚结束") or _is_stop_word(cmd)


FASAO_TIMEOUT = 15 * 60  # 发骚 active 超过 15 分钟无互动自动结束（防卡死）


def _check_fasao_timeout(cid: str):
    """发骚超时自动结束：active 超过 FASAO_TIMEOUT 自动结束，返回要发的消息或 None。"""
    fs = _get_fasao_state(cid)
    if not fs.get("active"):
        return None
    last_ts = fs.get("last_ts") or 0
    if last_ts and time.time() - last_ts > FASAO_TIMEOUT:
        log("⏱️ [" + cid[:8] + "] 发骚超时自动结束")
        return end_fasao(cid)
    return None


def end_fasao(cid: str) -> str:
    """结束发骚，返回收尾小结."""
    fs = _get_fasao_state(cid)
    full = fs.get("full", "")
    history = fs.get("history") or []
    end_text = ""
    if history:
        try:
            end_text = generate_scene_end(_strip_parenthesis(full), history)
        except Exception:
            end_text = ""
    end_text = (end_text or "").strip()
    _set_fasao_state(cid, {"active": False})
    if end_text:
        return "🎲 发骚结束啦～\n" + end_text
    return "🎲 发骚结束啦～"


def is_rewrite_cmd(cmd: str) -> str:
    """识别重新生成指令：/重写 /重新生成 /重来 /重新写。返回调整方向（空串=无方向），None=不是."""
    c = cmd.strip().lstrip("/").strip()
    if not c:
        return None
    for kw in ("重新生成", "重新写", "重写", "重来"):
        if c == kw:
            return ""
        if c.startswith(kw):
            return c[len(kw):].strip()
    return None


def rewrite_last(cid: str, cmd: str = "") -> str:
    """重新生成发骚的上一段（可带调整方向）."""
    fs = _get_fasao_state(cid)
    if not fs.get("active"):
        return "现在没有进行中的发骚哦～先 /发骚 试试～"
    history = fs.get("history") or []
    if not history:
        _set_fasao_state(cid, {"active": False})
        return "还没有发骚内容可以重写哦～先 /发骚 试试～"
    direction = is_rewrite_cmd(cmd) or ""
    try:
        text = generate_scene_rewrite(_strip_parenthesis(fs.get("combo", "")), history[:-1], history[-1], direction)
    except Exception:
        text = ""
    text = (text or "").strip()
    if not text:
        return "重写失败啦，再试一次～"
    history[-1] = text
    fs["history"] = history
    _set_fasao_state(cid, fs)
    _set_last_action(cid, {"type": "scene", "uid": fs.get("uid"), "full": fs.get("full", ""), "combo": fs.get("combo", ""), "output": text})
    return text


# ===== 调教功能 =====

def _get_dj_state(cid: str) -> dict:
    """读取调教状态."""
    return state_store.group_get(cid, "diaojiao", {}) or {}


def _set_dj_state(cid: str, dj: dict) -> None:
    """写入调教状态."""
    state_store.group_set(cid, "diaojiao", dj)


def is_diaojiao_cmd(cmd: str) -> str:
    """识别 /调教 指令，返回 目标+命令 文本（None 表示不是）."""
    for prefix in ["/调教", "/调教开始"]:
        if cmd.startswith(prefix):
            return cmd[len(prefix):].strip()
    return None


def is_dj_command_cmd(cmd: str) -> str:
    """识别调教命令：/命令 xxx 或 /xxx（直接斜杠，排除已知系统指令）。返回命令文本或 None."""
    if cmd.startswith("/命令"):
        return cmd[len("/命令"):].strip()
    # /xxx 直接斜杠命令（排除已知系统指令）
    if not cmd.startswith("/") or cmd.startswith("//"):
        return None
    body = cmd.lstrip("/").strip()
    if not body:
        return None
    if (is_help_cmd(cmd) or is_intro_cmd(cmd) or is_guide_cmd(cmd)
            or is_sync_cmd(cmd) or is_register_cmd(cmd) is not None
            or is_profile_cmd(cmd) != "" or is_rank_cmd(cmd)
            or is_fasao_cmd(cmd) or is_insult_cmd(cmd) is not None
            or is_fortune_cmd(cmd) or is_scene_cmd(cmd)
            or is_poem_cmd(cmd) is not None
            or is_continue_cmd(cmd) or is_rewrite_cmd(cmd) is not None or is_perf_start_cmd(cmd)
            or is_perf_end_cmd(cmd) or is_dj_end_cmd(cmd) or is_fasao_end_cmd(cmd)
            or is_diaojiao_cmd(cmd) is not None
            or is_story_start_cmd(cmd) or is_story_end_cmd(cmd)
            or is_vote_choice_cmd(cmd) is not None
            or is_vote_start_cmd(cmd)
            or is_summary_cmd(cmd) or is_devil_cmd(cmd) is not None
            or is_remember_cmd(cmd) is not None or is_recall_cmd(cmd)):
        return None
    return body


def is_dj_end_cmd(cmd: str) -> bool:
    """识别调教结束：/调教结束、停止、停下、结束 等."""
    return cmd.startswith("/调教结束") or _is_stop_word(cmd)


def _dj_target_text(uid: str) -> str:
    """调教目标资料文本（档案优先，零API）."""
    profile = get_profile(uid)
    if profile:
        return _archive_text_from_profile(profile)
    return _member_profile_text(uid, get_full_name(uid))


def _dj_end_text(target_uid: str, dj: dict) -> str:
    """调教收尾小结文本."""
    try:
        text = generate_diaojiao_end(_dj_target_text(target_uid), dj.get("round", 0))
        return (text or "").strip()
    except Exception:
        return ""


# 调教参与提醒（每轮命令后附带，群友可 /命令 xxx 或直接 /xxx 参与）
DJ_PARTICIPATE_HINT = "想参与的姐妹也来玩呀～发 /命令 xxx 或直接 /xxx 都行（加不加「命令」都可以）～30秒没人我就自己继续啦～"


def start_diaojiao(cid: str, cmd: str, sender_uid: str) -> str:
    """发起调教，返回第一轮命令消息."""
    rest = is_diaojiao_cmd(cmd)
    if not rest:
        return "格式：/调教 目标 命令（例如 /调教 糯糯 学狗叫）"
    target_uid, full, keywords = _parse_scene_target(rest)
    if not target_uid:
        return "没找到调教目标哦～换个人试试？"
    command = " ".join(keywords)
    if not command:
        # 没指定命令 → 根据发起人 + 目标 XP 自编
        sender_xp = (get_profile(sender_uid) or {}).get("xp") or []
        command = "自编一条命令（结合发起人的性癖：" + ("、".join(sender_xp) if sender_xp else "未知") + "，目标XP见目标资料）"
    _set_dj_state(cid, {
        "target_uid": target_uid,
        "active": True,
        "phase": "等回应",
        "round": 1,
        "last_activity": time.time(),
    })
    try:
        text = generate_diaojiao(_dj_target_text(target_uid), command)
    except Exception:
        text = ""
    text = (text or "").strip()
    if not text:
        text = full + "，小小糯命令你：" + command + "～"
    return "🔗 调教开始！目标：" + full + "～\n" + text + "\n\n" + DJ_PARTICIPATE_HINT


def _check_diaojiao(cid: str, pending: list) -> str:
    """调教状态流转，返回要发的消息（无则 None）."""
    dj = _get_dj_state(cid)
    if not dj.get("active"):
        return None
    target_uid = dj.get("target_uid")
    now = time.time()
    # 5分钟超时结束
    if now - dj.get("last_activity", now) > 300:
        end_text = _dj_end_text(target_uid, dj)
        _set_dj_state(cid, {"active": False})
        msg = "⏱️ 调教结束啦（5分钟没动静）～"
        if end_text:
            msg += "\n" + end_text
        return msg
    phase = dj.get("phase")
    if phase == "等回应":
        # 检测目标回应（非指令消息）
        for m in pending:
            if m.get("sent_by") == target_uid:
                t = (m.get("content") or {}).get("text", "").strip()
                if t and not t.startswith("/"):
                    dj["phase"] = "等命令"
                    dj["ask_at"] = now
                    dj["last_activity"] = now
                    dj["round"] = dj.get("round", 0) + 1
                    _set_dj_state(cid, dj)
                    _add_heat(cid, target_uid, HEAT_DIAOJIAO_ROUND)  # 被调教一轮
                    return "目标回应啦～谁想发下一条命令？发 /命令 xxx 或直接 /xxx 都行（目标自己也能发哦～）30秒没人发我就自己来～"
        return None
    if phase == "等命令":
        # 30秒冷场 → 催一句 + 自编命令
        if now - dj.get("ask_at", now) > 30:
            dj["phase"] = "等回应"
            dj["last_activity"] = now
            dj["round"] = dj.get("round", 0) + 1
            _set_dj_state(cid, dj)
            try:
                text = generate_diaojiao(_dj_target_text(target_uid), "自编一条新命令")
            except Exception:
                text = ""
            text = (text or "").strip()
            return "30秒没人发命令呀～那我继续啦～\n" + text + "\n\n" + DJ_PARTICIPATE_HINT
        return None
    return None


# ===== 公演功能 =====

def is_perf_start_cmd(cmd: str) -> bool:
    return cmd.startswith("/公演开始")


def is_perf_end_cmd(cmd: str) -> bool:
    """识别公演结束：/公演结束、停止、停下、结束 等."""
    return cmd.startswith("/公演结束") or _is_stop_word(cmd)


def is_review_cmd(cmd: str):
    """审核命令：返回 'approve'/'reject'/'ignore' 或 None."""
    if cmd.startswith("/同意"):
        return "approve"
    if cmd.startswith("/退回"):
        return "reject"
    if cmd.startswith("/忽略"):
        return "ignore"
    return None


def _get_perf_state(cid: str) -> dict:
    """读取公演状态。"""
    return state_store.group_get(cid, "perf", {}) or {}


def _set_perf_state(cid: str, perf: dict) -> None:
    """写入公演状态。"""
    state_store.group_set(cid, "perf", perf)


# ============ 投票功能（/投票）============
VOTE_DEFAULT_SECONDS = 2 * 60  # 默认 2 分钟自动结束

# 恶魔吐槽配置（自主毒舌，对事也对人）
DEVIL_BASE_PROB = 0.03       # 普通闲聊基础概率
DEVIL_SENSITIVE_PROB = 0.10  # 命中敏感词概率（封顶）
DEVIL_HOT_PROB = 0.08        # 多人热闹概率
DEVIL_COOLDOWN = 10 * 60     # 冷却 10 分钟
DEVIL_DAILY_MAX = 30         # 每日上限 30 次
DEVIL_HOT_MIN_SPEAKERS = 3   # 热闹阈值：最近 12 条内 ≥3 人发言
DEVIL_SENSITIVE_WORDS = [
    "骚", "发情", "母狗", "肉便器", "欠操", "高潮", "调教", "骚穴",
    "发浪", "骚水", "下贱", "淫荡", "发骚", "公演",
]
DEVIL_COLD_THRESHOLD = 2 * 60 * 60   # 冷清阈值：2 小时没人说话后首次 roll
DEVIL_COLD_PROB = 0.3            # 冷场吐槽概率：每次 roll 30%（不中也更新周期标记）
DEVIL_COLD_COOLDOWN = 30 * 60    # 冷场重试周期：30 分钟（无论吐没吐都重新计，无需有人说话）
DEVIL_COLD_QUIET_HOUR = 8        # 深夜静默：0-8 点不触发冷场吐槽
DEVIL_HOT_WINDOW = 3 * 60        # 热闹窗口：3 分钟内发言算"同时活跃"


def _get_vote_state(cid: str) -> dict:
    """读取投票状态。"""
    return state_store.group_get(cid, "vote", {}) or {}


def _set_vote_state(cid: str, vote: dict) -> None:
    """写入投票状态。"""
    state_store.group_set(cid, "vote", vote)


def is_vote_start_cmd(cmd: str) -> bool:
    return cmd.startswith("/投票")


def is_vote_end_cmd(cmd: str) -> bool:
    return cmd.startswith("/投票结束") or _is_stop_word(cmd)


def _normalize_choice(s: str):
    """归一化选项值：A/B/C... → 1/2/3...，返回数字字符串或 None."""
    s = s.strip().upper()
    if s in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
        return s
    mapping = {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5", "F": "6", "G": "7", "H": "8"}
    return mapping.get(s)


def is_vote_choice_cmd(cmd: str):
    """识别选择指令（/选 N 或 /N），返回归一化数字或 None. 投票与剧情共用."""
    c = cmd.strip().lstrip("/").strip()
    if not c:
        return None
    if c.startswith("选"):
        return _normalize_choice(c[1:].strip())
    return _normalize_choice(c)


def _extract_deadline_seconds(text: str):
    """提取截止时长，返回 (剩余文本, 秒数或 None)."""
    m = re.search(r'(\d+)\s*(分钟|小时|秒)', text)
    if not m:
        return text, None
    n = int(m.group(1))
    secs = {"分钟": 60, "小时": 3600, "秒": 1}[m.group(2)] * n
    remaining = (text[:m.start()] + text[m.end():]).strip()
    return remaining, secs


def _extract_mentioned_members(text: str, cid: str) -> list:
    """从自然语言提取点名的群成员（按用户顺序），返回简化昵称列表."""
    cleaned = text
    for sep in ["、", "，", ","]:
        cleaned = cleaned.replace(sep, " ")
    tokens = cleaned.split()
    profiles = load_profiles()
    mentioned = []
    for tok in tokens:
        if len(tok) < 2:
            continue
        uid = None
        for u, p in profiles.items():
            name = p.get("name", "")
            if name == tok or _simplify(name) == tok:
                uid = u
                break
        if not uid:
            for u, p in profiles.items():
                simp = _simplify(p.get("name", ""))
                if simp and len(simp) >= 2 and tok.startswith(simp):
                    uid = u
                    break
        if uid:
            label = get_name(uid) or _simplify(get_full_name(uid))
            if label and label not in mentioned:
                mentioned.append(label)
    return mentioned


def _extract_vote_title(text: str, mentioned: list) -> str:
    """去掉点名成员后的剩余文本作为标题（简单清理）."""
    title = text
    for name in mentioned:
        title = title.replace(name, " ")
    for w in ["、", "，", ",", " ", "？", "?", "！", "!", "呢", "呀", "啊", "嘛", "～", "~"]:
        title = title.replace(w, "")
    title = title.strip()
    return title or "投票"


def _vote_members_text(cid: str) -> str:
    """当前群发言成员名单（供 AI 生成"选人"类选项）."""
    msgs = get_messages_all(cid, max_count=200)
    uids = []
    for m in msgs:
        uid = m.get("sent_by", "")
        if uid and not is_self(uid) and uid not in uids:
            uids.append(uid)
    names = [get_name(uid) for uid in uids if get_name(uid)]
    return "、".join(names[:30])


def _dedupe_options(options: list) -> list:
    seen = []
    for o in options:
        o = str(o).strip()
        if o and o not in seen:
            seen.append(o)
    return seen


def start_vote(cid: str, cmd: str, sender_uid: str) -> str:
    """发起投票：解析三种方式，返回投票消息."""
    rest = cmd[len("/投票"):].strip() if cmd.startswith("/投票") else cmd
    if not rest:
        return "格式：/投票 主题 | 选项1 | 选项2（或 /投票 主题 让我生成选项，可加 5分钟 定时）"
    rest, deadline_secs = _extract_deadline_seconds(rest)
    title = rest
    options = []
    if "|" in rest:
        parts = [p.strip() for p in rest.split("|") if p.strip()]
        title = parts[0]
        options = parts[1:]
    else:
        mentioned = _extract_mentioned_members(rest, cid)
        if len(mentioned) >= 2:
            title = _extract_vote_title(rest, mentioned)
            options = mentioned
        else:
            try:
                options = generate_vote_options(rest, _vote_members_text(cid))
            except Exception:
                options = []
            if len(options) < 2:
                return "没想好选项呢，你自己列一下吧：/投票 主题 | 选项1 | 选项2"
    options = _dedupe_options(options)[:6]
    if len(options) < 2:
        return "选项太少啦，至少 2 个哦～格式 /投票 主题 | 选项1 | 选项2"
    _set_vote_state(cid, {
        "active": True,
        "title": title,
        "options": options,
        "votes": {},
        "starter": sender_uid,
        "deadline": time.time() + (deadline_secs or VOTE_DEFAULT_SECONDS),
        "last_remind": time.time(),
    })
    lines = ["📊 投票：" + title]
    for i, opt in enumerate(options, 1):
        lines.append("【" + str(i) + "】" + opt)
    if deadline_secs:
        if deadline_secs >= 60:
            footer = "（发 /选 X 或 /X 投票（X=序号），可改票，" + str(deadline_secs // 60) + "分钟倒计时～）"
        else:
            footer = "（发 /选 X 或 /X 投票（X=序号），可改票，" + str(deadline_secs) + "秒倒计时～）"
    else:
        footer = "（发 /选 X 或 /X 投票（X=序号），可改票，2分钟倒计时，/投票结束 可提前结束～）"
    lines.append(footer)
    return "\n".join(lines)


def _record_vote(cid: str, uid: str, choice: str) -> None:
    """记录投票（以最后一次为准），静默不回复."""
    vote = _get_vote_state(cid)
    if not vote.get("active"):
        return
    votes = vote.get("votes") or {}
    votes[uid] = choice
    vote["votes"] = votes
    _set_vote_state(cid, vote)


def _finish_vote(cid: str) -> str:
    """结束投票：统计 + 结算加分 + 清空，返回结果消息."""
    vote = _get_vote_state(cid)
    if not vote.get("active"):
        return ""
    title = vote.get("title", "投票")
    options = vote.get("options") or []
    votes = vote.get("votes") or {}
    counts = {}
    for v in votes.values():
        counts[v] = counts.get(v, 0) + 1
    total = len(votes)
    lines = ["📊 投票结果：" + title]
    for i, opt in enumerate(options, 1):
        key = str(i)
        n = counts.get(key, 0)
        pct = (n * 100 // total) if total else 0
        voters = [get_name(uid) for uid, v in votes.items() if v == key]
        voter_str = "、".join([x for x in voters if x]) if voters else "无"
        lines.append("【" + str(i) + "】" + opt + "　" + str(n) + "票(" + str(pct) + "%)　" + voter_str)
    starter = vote.get("starter", "")
    if starter and not is_self(starter):
        _add_heat(cid, starter, HEAT_VOTE_STARTER)
    for uid in votes.keys():
        if uid and not is_self(uid) and uid != starter:
            _add_heat(cid, uid, HEAT_VOTE_VOTER)
    _set_vote_state(cid, {})
    lines.append("🎉 结算：发起人 +8，投票的人各 +5")
    return "\n".join(lines)


def _check_vote(cid: str) -> str:
    """投票定时检查：每 30 秒提醒剩余时间，截止到 → 自动结束."""
    vote = _get_vote_state(cid)
    if not vote.get("active"):
        return ""
    now = time.time()
    remaining = vote.get("deadline", 0) - now
    if remaining <= 0:
        return _finish_vote(cid)
    if now - vote.get("last_remind", 0) >= 30:
        vote["last_remind"] = now
        _set_vote_state(cid, vote)
        return "⏰ 还有 " + str(int(remaining)) + " 秒，想投的快 /选 X 或 /X 哦～"
    return ""


# ============ 剧情游戏（/剧情）============
STORY_MAX_ROUND = 6
STORY_VOTE_SECONDS = 60


def _get_story_state(cid: str) -> dict:
    """读取剧情状态。"""
    return state_store.group_get(cid, "story", {}) or {}


def _set_story_state(cid: str, story: dict) -> None:
    """写入剧情状态。"""
    state_store.group_set(cid, "story", story)


def _active_play_hint(cid: str) -> str:
    """返回当前独占型玩法进行中的「打断保护」提示，无则返回 None."""
    if _get_perf_state(cid).get("active"):
        return "🎭 公演进行中哦～暂无法响应，想参与就直接发言，等公演结束后再试～"
    if _get_fasao_state(cid).get("active"):
        return "🎲 发骚进行中哦～暂无法响应其他指令，想继续发 /继续，想结束发 /结束～"
    if _get_dj_state(cid).get("active"):
        return "🔗 调教进行中哦～暂无法响应其他指令，想参与发 /命令 xxx，想结束发 /调教结束～"
    if _get_story_state(cid).get("active"):
        return "🎬 剧情进行中哦～暂无法响应其他指令，想投票发 /选 X，想结束发 /剧情结束～"
    return None


def is_story_start_cmd(cmd: str) -> bool:
    return cmd.startswith("/剧情开始") or cmd.strip() == "/剧情"


def is_story_end_cmd(cmd: str) -> bool:
    return cmd.startswith("/剧情结束") or _is_stop_word(cmd)


def start_story(cid: str, cmd: str, sender_uid: str) -> str:
    """发起剧情游戏：解析主角+自定义剧情，生成开局，设置状态，返回开局消息."""
    rest = ""
    if cmd.startswith("/剧情开始"):
        rest = cmd[len("/剧情开始"):].strip()
    protagonist_uid = None
    protagonist = ""
    custom = ""
    if rest:
        tokens = rest.split()
        # 档案本地匹配（零 API，可多次尝试）
        for i in range(len(tokens), 0, -1):
            name_part = " ".join(tokens[:i])
            uid, _ = _find_in_profiles(name_part)
            if uid and not is_self(uid):
                protagonist_uid = uid
                protagonist = get_full_name(uid)
                custom = " ".join(tokens[i:])
                break
        # 消息里匹配（一次 API）
        if not protagonist_uid:
            msgs = get_messages_all(cid, max_count=200)
            for i in range(len(tokens), 0, -1):
                name_part = " ".join(tokens[:i])
                uid2, full2 = _find_user_in_msgs(name_part, msgs)
                if uid2 and not is_self(uid2):
                    protagonist_uid = uid2
                    protagonist = full2
                    custom = " ".join(tokens[i:])
                    break
        # 匹配不到 → 整个当自定义剧情，主角随机
        if not protagonist_uid:
            custom = rest
    # 未指定主角 → 随机抽一个（排除发起者）
    if not protagonist_uid:
        protagonist_uid = _random_member_uid(exclude={sender_uid})
        protagonist = get_full_name(protagonist_uid) if protagonist_uid else ""
    if not protagonist_uid:
        return "群里还没有可参与的人呢，先聊几句再来开剧情～"
    opening = generate_story_start(protagonist, custom)
    opening = (opening or "").strip()
    _set_story_state(cid, {
        "active": True,
        "phase": "等投票",
        "round": 1,
        "max_round": STORY_MAX_ROUND,
        "protagonist_uid": protagonist_uid,
        "protagonist": protagonist,
        "custom": custom,
        "votes": {},
        "vote_deadline": time.time() + STORY_VOTE_SECONDS,
        "remind_30": False,
        "remind_10": False,
        "history": [opening],
    })
    header = "🎬 剧情开始！女主：" + protagonist
    footer = "（" + str(STORY_VOTE_SECONDS) + "秒内 /选 1/2/3 或 /1/2/3 投票，可改票～）"
    return header + "\n" + opening + "\n" + footer


def _record_story_vote(cid: str, uid: str, choice: str) -> None:
    """记录投票（以最后一次为准），静默不回复."""
    story = _get_story_state(cid)
    if not story.get("active"):
        return
    votes = story.get("votes") or {}
    votes[uid] = choice
    story["votes"] = votes
    _set_story_state(cid, story)


def _story_vote_result(cid: str) -> str:
    """统计投票，返回本轮选中选项(1/2/3)。平票随机、无人投随机兜底."""
    story = _get_story_state(cid)
    votes = story.get("votes") or {}
    if not votes:
        return random.choice(["1", "2", "3"])
    counts = {}
    for v in votes.values():
        key = "1" if v in ("1", "A") else ("2" if v in ("2", "B") else "3")
        counts[key] = counts.get(key, 0) + 1
    max_n = max(counts.values())
    top = [k for k, n in counts.items() if n == max_n]
    return random.choice(top)


def _check_story(cid: str) -> str:
    """剧情状态流转：提醒投票 / 推进剧情 / 结局。返回要发送的文本（空=无需发送）."""
    story = _get_story_state(cid)
    if not story.get("active") or story.get("phase") != "等投票":
        return ""
    now = time.time()
    remaining = story.get("vote_deadline", 0) - now
    if 10 < remaining <= 30 and not story.get("remind_30"):
        story["remind_30"] = True
        _set_story_state(cid, story)
        return "⏰ 还有 30 秒，想投的快 /选 1/2/3 哦～"
    if 0 < remaining <= 10 and not story.get("remind_10"):
        story["remind_10"] = True
        _set_story_state(cid, story)
        return "⏰ 还有 10 秒，最后机会啦～"
    if remaining <= 0:
        return _advance_story(cid)
    return ""


def _finish_story(cid: str) -> str:
    """生成结局 + 结算 + 清空状态，返回结局消息."""
    story = _get_story_state(cid)
    if not story.get("active"):
        return ""
    history = story.get("history") or []
    history_text = "\n\n".join(history) if history else ""
    ending = generate_story_end(history_text)
    ending = (ending or "").strip()
    votes = story.get("votes") or {}
    protagonist_uid = story.get("protagonist_uid", "")
    protagonist = story.get("protagonist", "")
    for uid in votes.keys():
        if uid and not is_self(uid):
            _add_heat(cid, uid, HEAT_STORY_VOTER)
    if protagonist_uid and not is_self(protagonist_uid):
        _add_heat(cid, protagonist_uid, HEAT_STORY_PROTAGONIST)
    _set_story_state(cid, {})
    lines = ["🎬 剧情结束！女主：" + protagonist]
    if ending:
        lines.append(ending)
    if votes:
        lines.append("🎉 结算：参与投票的各 +8，女主 +15")
    return "\n".join(lines)


def _advance_story(cid: str) -> str:
    """推进剧情：统计投票，生成下一段或结局。返回要发送的文本."""
    story = _get_story_state(cid)
    if not story.get("active"):
        return ""
    round_no = story.get("round", 1)
    max_round = story.get("max_round", STORY_MAX_ROUND)
    history = story.get("history") or []
    choice = _story_vote_result(cid)
    choice_label = "选项" + choice
    history_text = "\n\n".join(history) if history else ""
    if round_no >= max_round:
        return _finish_story(cid)
    # 推进：生成下一段（结尾必须带【1】【2】【3】选项，缺了就重试）
    next_part = generate_story_step(history_text, choice_label)
    next_part = (next_part or "").strip()
    for _ in range(2):
        if "【1】" in next_part and "【2】" in next_part and "【3】" in next_part:
            break
        retry = generate_story_step(history_text, choice_label)
        retry = (retry or "").strip()
        if retry:
            next_part = retry
    story["history"] = history + [next_part]
    story["round"] = round_no + 1
    story["votes"] = {}
    story["vote_deadline"] = time.time() + STORY_VOTE_SECONDS
    story["remind_30"] = False
    story["remind_10"] = False
    _set_story_state(cid, story)
    footer = "（" + str(STORY_VOTE_SECONDS) + "秒内 /选 1/2/3 或 /1/2/3 投票，可改票～）"
    return next_part + "\n" + footer


def _get_perf_content(cid: str, start_id: str) -> str:
    """获取公演内容（旧→新）：从 /公演开始 之后到 /公演结束，只保留用户原始对话，跳过指令与 bot 消息."""
    msgs = get_messages_all(cid, max_count=1000)  # 旧→新
    lines = []
    start_i = 0
    for i, m in enumerate(msgs):
        if m.get("message_id") == start_id:
            start_i = i  # 从 /公演开始 本身开始（不回溯预热）
            break
    for m in msgs[start_i:]:
        uid = m.get("sent_by", "")
        if is_self(uid):
            continue  # 跳过 bot 消息（小小糯的丰富/总结），只保留用户原始对话
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text:
            continue
        if text.startswith("/"):
            if text.startswith("/公演结束"):
                break  # 到公演结束停止，不纳入结束后闲聊
            continue  # 跳过指令（/公演开始、/继续、/命令 等）
        lines.append(get_name(uid) + ": " + text)
    return "\n".join(lines)


def _get_perf_content_full(cid: str, start_id: str) -> str:
    """获取公演完整时间线（旧→新）：用户原始对话 + 小小糯丰富（标记【小小糯参考】），供小说择优整合."""
    msgs = get_messages_all(cid, max_count=1000)
    lines = []
    start_i = 0
    for i, m in enumerate(msgs):
        if m.get("message_id") == start_id:
            start_i = i
            break
    for m in msgs[start_i:]:
        uid = m.get("sent_by", "")
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text:
            continue
        if is_self(uid):
            # 小小糯（bot）：去掉 🤖，排除非剧情（开场/排行/午安），合并多行为单行
            t = text.replace("🤖", "").strip()
            if any(kw in t for kw in ("🎬", "🔥", "排行", "午安", "公演开始")):
                continue
            if len(t) < 30:
                continue
            t = " ".join(t.split())  # 合并内部换行为单行，避免被当成多条对话
            lines.append("【小小糯参考】" + t)
            continue
        if text.startswith("/"):
            if text.startswith("/公演结束"):
                break
            continue
        lines.append(get_name(uid) + ": " + text)
    return "\n".join(lines)


def _perf_heat_target(cid: str) -> str:
    """识别公演里的被调教者（M方），返回 uid 或空."""
    perf = _get_perf_state(cid)
    content = _get_perf_content(cid, perf.get("start_id", ""))
    if not content:
        return ""
    # M方特征词（第一人称自我发情/认主/求饶，区别于S方的第二人称命令羞辱）
    m_kws = ["我发情", "我想要", "我湿", "我是", "我在", "汪汪", "求", "上头",
             "认主", "饶", "迫不及待", "干自己"]
    scores = {}
    for line in content.split("\n"):
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        text = line.split(":", 1)[1]
        hit = sum(1 for kw in m_kws if kw in text)
        if hit:
            scores[name] = scores.get(name, 0) + hit
    if not scores:
        return ""
    # 命中最多者 = M方；跳过糯糯（主持）和小小糯
    for name, _ in sorted(scores.items(), key=lambda kv: -kv[1]):
        uid = _find_uid_by_name(name)
        if uid and not is_self(uid) and uid != OWNER_UID:
            return uid
    return ""


def _perf_m_name(content: str) -> str:
    """从公演内容识别被调教方（M）的昵称（基于第一人称发情/求饶特征词，不拉消息）."""
    m_kws = ["我发情", "我想要", "我湿", "我是", "我在", "汪汪", "求", "上头",
             "认主", "饶", "迫不及待", "干自己"]
    scores = {}
    for line in content.split("\n"):
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        text = line.split(":", 1)[1]
        hit = sum(1 for kw in m_kws if kw in text)
        if hit:
            scores[name] = scores.get(name, 0) + hit
    if not scores:
        return ""
    return sorted(scores.items(), key=lambda kv: -kv[1])[0][0]


def _extract_novel_title(novel: str) -> str:
    """提取小说标题（第一个 # 行）."""
    for line in novel.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return "公演小说"


def _novel_filename(novel: str) -> str:
    """生成小说文件名：标题 + 生成时间."""
    title = _extract_novel_title(novel)
    for ch in '/\\:*?"<>|':
        title = title.replace(ch, "")
    title = title.strip() or "公演小说"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return title + "_" + ts + ".md"


def _novel_save_path(cid: str, novel: str) -> str:
    """返回小说完整保存路径（novels/<群名>/<标题>_<时间>.md），自动建目录."""
    fname = _novel_filename(novel)
    gname = ""
    cache = _group_name_cache.get(cid, {"name": "", "ts": 0.0})
    if time.time() - cache["ts"] < 300 and cache["name"]:
        gname = cache["name"]
    else:
        try:
            gname = get_chatroom_name(cid) or ""
        except Exception:
            gname = ""
        cache["name"] = gname
        cache["ts"] = time.time()
        _group_name_cache[cid] = cache
    for ch in '/\\:*?"<>|':
        gname = gname.replace(ch, "")
    gname = gname.strip() or cid[:8]
    d = ROOT / "novels" / gname
    d.mkdir(parents=True, exist_ok=True)
    return str(d / fname)


def split_novel(text: str, max_lines: int = 10, max_bytes: int = 1000):
    """分段美化：优先在 ## 章节标题处断，每段 ≤10行 且 ≤1000字节."""
    lines = text.split("\n")
    chunks, cur_lines, cur_bytes = [], [], 0
    for line in lines:
        lb = len((line + "\n").encode('utf-8'))
        is_heading = line.strip().startswith("## ")
        if is_heading and cur_lines:
            chunks.append("\n".join(cur_lines))
            cur_lines, cur_bytes = [], 0
        if (len(cur_lines) >= max_lines or cur_bytes + lb > max_bytes) and cur_lines:
            chunks.append("\n".join(cur_lines))
            cur_lines, cur_bytes = [], 0
        cur_lines.append(line)
        cur_bytes += lb
    if cur_lines:
        chunks.append("\n".join(cur_lines))
    return chunks


def parse_time_range(cmd: str):
    """从指令解析时间范围，返回 (start_utc, end_utc) 两个 datetime（UTC）."""
    from datetime import timedelta, timezone
    now_utc = datetime.now(timezone.utc)
    # 北京时间 = UTC + 8
    beijing = timezone(timedelta(hours=8))
    now_bj = now_utc.astimezone(beijing)

    if "昨天" in cmd:
        start_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
    elif "今天" in cmd:
        start_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
        end_bj = now_bj
    else:
        # 默认最近 24 小时
        start_bj = now_bj - timedelta(hours=24)
        end_bj = now_bj

    return start_bj.astimezone(timezone.utc), end_bj.astimezone(timezone.utc)


def summarize_chat(cmd: str) -> str:
    """拉取群聊记录并总结."""
    from datetime import timezone, timedelta
    msgs = get_messages_all(_ACTIVE_CID)
    if not msgs:
        return "群聊记录为空，没有可总结的内容。"

    start_utc, end_utc = parse_time_range(cmd)

    lines = []
    for m in msgs:
        # 过滤 agent 自己的消息，避免自我引用循环
        if is_self(m.get("sent_by")):
            continue
        # 解析时间
        try:
            s = m.get("sent_at", "")
            s = s.split(".")[0]  # 去毫秒
            if s.endswith("+00"):
                s = s[:-3]
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if not (start_utc <= dt <= end_utc):
            continue
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text:
            continue
        name = get_name(m.get("sent_by", ""))
        t = dt.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M")
        lines.append(f"[{t}] {name}: {text}")

    if not lines:
        return "这段时间群里没有聊天记录。"

    # 截断，避免太长
    if len(lines) > 200:
        lines = lines[-200:]
    return "\n".join(lines)


# ===== 公演 handler =====

async def _perf_send(sender, text: str, cid: str, dry: bool):
    """发送公演消息（带 🤖 前缀）."""
    text = (text or "").strip()
    if not text:
        return
    if dry:
        log("[dry] " + text[:50])
        return
    ok = await sender.send("🤖 " + text, cid)
    log("✅" if ok else "❌")


async def _handle_perf_start(sender, cid: str, start_id: str, dry: bool):
    perf = {
        "active": True,
        "start_id": start_id,
        "last_interact_id": start_id,
        "last_interact_ts": time.time(),
        "interact_seq": 0,
        "review_status": "none",
    }
    _set_perf_state(cid, perf)
    await _perf_send(sender, "🎬 公演开始啦～姐姐们请开始表演，小小糯会实时丰富剧情、点评、羞辱哦～（结束发 /公演结束）", cid, dry)


async def _scheduled_pushes(sender, dry: bool) -> None:
    """定时问候（早/中/晚）+ 记忆汇报：用主 sender 发（页面常驻，秒发）。

    相比原来的 entrypoint「到点另起进程 → 冷启浏览器」：
    - 不再有 30 秒整页加载超时的风险（认证群页面本来就常驻）
    - 带补发窗口：机器人在到点后 90 分钟内恢复，仍然会补发（原来错过就永远没了）
    - 每群独立打点（state[cid]["last_greet_xxx"]），重启/部署不会重复发
    """
    if dry:
        return
    set_persona_cid("")  # 广播文案（问候/记忆汇报）一律用默认人设，别串到房间群管家口吻
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    cur = now.hour * 60 + now.minute
    st = load_state()
    changed = False
    for period, hour in (("morning", 8), ("noon", 12), ("evening", 22)):
        # 窗口：[整点, 整点+90 分钟]；超出窗口就不补了（避免半夜恢复时补发早安）
        if not (hour * 60 <= cur <= hour * 60 + 90):
            continue
        key = "last_greet_" + period
        todo = [c for c in PUSH_CIDS if (st.get(c) or {}).get(key) != today]
        if not todo:
            continue
        try:
            greeting = (await asyncio.to_thread(generate_greeting, period, today) or "").strip()
        except Exception as e:
            log("问候生成失败(" + period + "): " + str(e)[:60])
            greeting = ""
        if not greeting:
            greeting = "姐姐们好呀～"
        for c in todo:
            ok = await sender.send("🤖 " + greeting, c)
            if ok:
                gs = st.get(c) or {}
                gs[key] = today
                st[c] = gs
                changed = True
            log("greet[" + period + "] " + c[:8] + ": " + ("✅" if ok else "❌"))
        if changed:
            save_state(st)
        if period == "noon":
            # 午间顺带发每日记忆汇报
            for c in PUSH_CIDS:
                if (st.get(c) or {}).get("last_memreport") == today:
                    continue
                try:
                    await _publish_memory_report(sender, c, dry)
                except Exception as e:
                    log("记忆汇报失败: " + str(e)[:60])
                gs = st.get(c) or {}
                gs["last_memreport"] = today
                st[c] = gs
                changed = True
            if changed:
                save_state(st)


async def _perf_end_bg(sender, cid: str, dry: bool) -> None:
    """后台跑公演收尾 + 小说生成（不堵主循环），异常只记日志."""
    try:
        await _handle_perf_end(sender, cid, dry)
        log("🎬 [" + cid[:8] + "] 公演收尾（后台）完成")
    except Exception as e:
        log("🎬 [" + cid[:8] + "] 公演收尾（后台）异常: " + str(e)[:100])


async def _handle_perf_end(sender, cid: str, dry: bool):
    perf = _get_perf_state(cid)
    start_id = perf.get("start_id", "")
    content = await asyncio.to_thread(_get_perf_content, cid, start_id)
    if not content:
        perf["active"] = False
        _set_perf_state(cid, perf)
        await _perf_send(sender, "公演内容为空，没法生成小说啦～", cid, dry)
        return

    # 1. 点评整个公演
    ending = await asyncio.to_thread(generate_perf_ending, content)
    await _perf_send(sender, "🎬 公演结束！小小糯点评：\n" + ending, cid, dry)

    # 1.5 等待参与者补充收尾（20秒，避免漏掉公演结束后的补充台词）
    await _perf_send(sender, "📝 想补充收尾的姐妹还有 20 秒哦～20 秒后我开始生成小说～", cid, dry)
    await asyncio.sleep(20)
    content = await asyncio.to_thread(_get_perf_content, cid, start_id)

    # 2. 提示生成中
    await _perf_send(sender, "📝 小说正在生成中，等糯糯姐审核后发送哦～", cid, dry)

    # 3. 生成小说（带角色标注，帮助 AI 识别 M 方）
    m_name = await asyncio.to_thread(_perf_m_name, content)
    novel_content = content
    if m_name:
        novel_content = "【被调教方（M）】：" + m_name + "\n\n" + content
    try:
        novel = (await asyncio.to_thread(generate_perf_novel_chunked, novel_content)).strip().replace("🤖", "")
    except Exception:
        novel = ""
    if not novel:
        perf["active"] = False
        _set_perf_state(cid, perf)
        await _perf_send(sender, "小说生成失败了，可以重新 /公演结束 再试哦～", cid, dry)
        return

    # 4. 保存
    path = _novel_save_path(cid, novel)
    with open(path, "w", encoding="utf-8") as f:
        f.write(novel)

    perf.update({
        "active": False,
        "novel_path": path,
        "novel_title": _extract_novel_title(novel),
        "review_status": "pending",
        "review_notified_ts": time.time(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _set_perf_state(cid, perf)
    await _perf_send(sender, "小说《" + perf["novel_title"] + "》已生成～请糯糯姐审核：/同意 发送｜/退回 重写｜/忽略 不再提醒", cid, dry)
    # 事件驱动：直连微信提醒糯糯审核（不等定时任务）
    send_weixin("📝【小小糯】公演小说《" + perf["novel_title"] + "》已生成，待审核～\n文件：" + path +
                "\n请在群里回复：/同意 发送｜/退回 重写｜/忽略 不再提醒")


async def _handle_review(sender, cid: str, action: str, dry: bool):
    perf = _get_perf_state(cid)
    if action == "ignore":
        perf["review_status"] = "ignored"
        _set_perf_state(cid, perf)
        await _perf_send(sender, "好哦～不提醒啦，小说先存着～", cid, dry)
        return
    if action == "reject":
        start_id = perf.get("start_id", "")
        await _perf_send(sender, "收到退回～重新生成中...", cid, dry)
        content = await asyncio.to_thread(_get_perf_content, cid, start_id)
        m_name = await asyncio.to_thread(_perf_m_name, content)
        novel_content = content
        if m_name:
            novel_content = "【被调教方（M）】：" + m_name + "\n\n" + content
        novel = (await asyncio.to_thread(generate_perf_novel_chunked, novel_content)).strip().replace("🤖", "")
        if not novel:
            await _perf_send(sender, "重写失败了，再试一次 /退回 吧～", cid, dry)
            return
        path = _novel_save_path(cid, novel)
        with open(path, "w", encoding="utf-8") as f:
            f.write(novel)
        perf.update({"novel_path": path, "novel_title": _extract_novel_title(novel),
                     "review_status": "pending", "review_notified_ts": time.time()})
        _set_perf_state(cid, perf)
        await _perf_send(sender, "已重新生成《" + perf["novel_title"] + "》，请糯糯姐再审核～", cid, dry)
        send_weixin("📝【小小糯】公演小说《" + perf["novel_title"] + "》已重新生成，待审核～\n文件：" + path +
                    "\n请在群里回复：/同意 发送｜/退回 重写｜/忽略 不再提醒")
        return
    if action == "approve":
        path = perf.get("novel_path", "")
        try:
            novel = open(path, encoding="utf-8").read()
        except Exception:
            novel = ""
        if not novel:
            await _perf_send(sender, "没找到小说文件，重新 /公演结束 试试吧～", cid, dry)
            return
        perf["review_status"] = "approved"
        _set_perf_state(cid, perf)
        chunks = split_novel(novel)
        await _perf_send(sender, "✅ 审核通过～开始发送《" + perf.get("novel_title", "") + "》～", cid, dry)
        for i, chunk in enumerate(chunks):
            decorated = "📖【" + str(i + 1) + "/" + str(len(chunks)) + "】\n\n" + chunk
            await _perf_send(sender, decorated, cid, dry)
            await asyncio.sleep(1.5)
        await _perf_send(sender, "（完）", cid, dry)


def _check_perf_interact(cid: str, msgs: list) -> str:
    """公演进行中判断是否触发互动（只保留"丰富"）。触发条件：有新消息且距上次≥10分钟."""
    perf = _get_perf_state(cid)
    last_id = perf.get("last_interact_id", "")
    new_msgs = []
    found = False
    for m in msgs:  # 旧→新
        if not found:
            if m.get("message_id") == last_id:
                found = True
            continue
        uid = m.get("sent_by", "")
        if is_self(uid):
            continue
        text = (m.get("content") or {}).get("text", "").strip()
        if text and "加入了群聊" not in text:
            new_msgs.append(m)
    if not new_msgs:
        return ""

    # 触发条件：距上次互动 ≥ 10 分钟（600秒），且期间有新消息
    now = time.time()
    elapsed = now - perf.get("last_interact_ts", 0)
    if elapsed < 600:
        return ""

    perf["last_interact_id"] = msgs[-1]["message_id"]
    perf["last_interact_ts"] = now
    seq = perf.get("interact_seq", 0)
    perf["interact_seq"] = seq + 1
    _set_perf_state(cid, perf)

    # 增量喂料：本轮新增消息为主 + 前文一小段衔接
    new_text = "\n".join(
        get_name(m.get("sent_by", "")) + ": " + (m.get("content") or {}).get("text", "").strip()
        for m in new_msgs
    )
    content = _get_perf_content(cid, perf.get("start_id", ""))
    context = content[-200:] if content else ""
    tail = context + "\n\n【本轮新发生的内容】\n" + new_text

    # 记忆去重：把上一轮互动文本写进 prompt，要求避开
    prev_text = perf.get("last_interact_text", "")
    if prev_text:
        tail += "\n\n【上一轮小小糯已经说过的内容（务必避开重复）】\n" + prev_text

    reply = generate_perf_enrich(tail)
    perf["last_interact_text"] = reply
    _set_perf_state(cid, perf)
    return reply


FAST_INTERVAL = 0.8  # 最快轮询间隔（秒）


def _max_interval() -> float:
    """当前时段的退避封顶间隔：早8~凌晨2=5s，凌晨2~早8=10s."""
    hour = datetime.now().hour  # 本地时间（北京时间）
    if 2 <= hour < 8:
        return 10.0
    return 5.0


def _next_interval(cur: float, had_new: bool) -> float:
    """退避一步：有新消息重置最快，没消息翻倍封顶，全程 jitter."""
    if had_new:
        return FAST_INTERVAL * random.uniform(0.9, 1.1)
    max_i = _max_interval()
    return min(cur * 2 * random.uniform(0.9, 1.1), max_i * random.uniform(0.95, 1.05))


# ---------------------------------------------------------------- 出站队列（P3）+ Bot 路由（P4）
# 防御性导入：deploy.ps1 需递归上传才能带上 runtime/ 子包。
# 缺包时静默退回直发，绝不让 bot 起不来。
_RUNTIME_IMPORT_ERR = ""                                   # 无条件定义，避免条件定义隐患
try:
    from runtime.outbox import OutboxSender, OutboxStore
    from runtime.routing import RoutingSender
    from runtime.transport_bot import DzmmBotSender
    _RUNTIME_IMPORT_OK = True
except Exception as _e:                                    # noqa: BLE001
    _RUNTIME_IMPORT_OK = False
    _RUNTIME_IMPORT_ERR = str(_e)[:120]


def _truthy_env(name: str, default: str = "off") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("on", "1", "yes", "true")


def _outbox_enabled() -> bool:
    """DZMM_OUTBOX=off/0/no/false 可退回直发（回滚开关）。默认开。"""
    v = (os.environ.get("DZMM_OUTBOX") or "on").strip().lower()
    return v not in ("off", "0", "no", "false")


def _log_route(event: str, cid: str, detail: str = "") -> None:
    """路由审计。account 太频繁不记；bot_* 事件都记（切换期观察用）。"""
    if event == "account":
        return
    log("bot路由: {} cid={} {}".format(event, (cid or "")[:8], str(detail)[:80]))


def _wrap_with_bot_routing(account):
    """把装了官方 Bot 的群路由到 Bot。**默认关闭** —— 接线但不上线。

    `DZMM_BOT_ROUTING=on` 才启用；缺 token/cid 或初始化异常一律退回账号。
    切换时机由人决定，不由代码替人决定。
    """
    if not _RUNTIME_IMPORT_OK:
        return account
    if not _truthy_env("DZMM_BOT_ROUTING", "off"):
        log("bot路由: 未启用（DZMM_BOT_ROUTING=off），全部走账号")
        return account
    token = (os.environ.get("DZMM_BOT_TOKEN") or "").strip()
    cid = (os.environ.get("DZMM_BOT_CID") or "").strip()
    if not token or not cid:
        log("bot路由: 缺少 DZMM_BOT_TOKEN 或 DZMM_BOT_CID，退回账号")
        return account
    try:
        origin = (os.environ.get("DZMM_BOT_ORIGIN") or ORIGIN).rstrip("/")
        bot = DzmmBotSender(origin, token, proxy=(os.environ.get("DZMM_PROXY") or None))
        log("bot路由: 已启用 cid={} origin={}".format(cid[:8], origin))
        return RoutingSender(account, {cid: bot}, on_route=_log_route)
    except Exception as e:                                 # noqa: BLE001
        log("bot路由: 初始化失败，退回账号：" + str(e)[:100])
        return account


def _log_runtime_status() -> None:
    """启动时打印 P5 / P7 的运行时状态。

    没有这几行，这些能力生效与否在日志里看不出来（它们默认不输出任何东西）。
    """
    try:
        from session_guard import version as _sver
        from throttle_guard import stats as _tstats
        s = _tstats() or {}
        b = s.get("bucket") or {}
        log("P5: 会话锁=on(version={}) 限流={} rate={}/min burst={}".format(
            _sver(), "on" if s.get("enabled") else "off",
            b.get("rate_per_min"), b.get("capacity")))
    except Exception as e:                                 # noqa: BLE001
        log("P5: 状态未知（" + str(e)[:60] + "）")
    try:
        import state_store
        st = state_store.stats()
        log("P7: 存储={} db={} counts={}".format(
            st.get("backend"), st.get("db") or "-", st.get("counts") or {}))
    except Exception as e:                                 # noqa: BLE001
        log("P7: 状态未知（" + str(e)[:60] + "）")
    try:
        import runtime.prompts as _prompts
        ps = _prompts.stats()
        # 这行是 P8 第 3 步的自证：prompt 到底来自文件还是代码里的默认值。
        # 外置最常见的坑是"文件没挂上 → 悄悄退回默认值"，不打印就看不出来。
        log("P8: prompt dir={} 来自文件={}/{} 磁盘文件={}".format(
            ps.get("dir"), ps.get("from_file"), ps.get("total"),
            ps.get("files_on_disk")))
    except Exception as e:                                 # noqa: BLE001
        log("P8: 无 runtime.prompts，prompt 全部用代码默认值（"
            + str(e)[:50] + "）")


def _wrap_with_outbox(sender, dry: bool):
    """组装发送链：`OutboxSender( RoutingSender( 账号 ) )`。

    出站队列放**最外层** —— 无论最终走 Bot 还是账号，都要落库、串行、可审计。
    任何一层异常都退回更简单的形态，不阻断 bot 启动。
    """
    if dry:
        return sender
    if not _RUNTIME_IMPORT_OK:
        log("outbox: runtime 包不可用，直发（" + _RUNTIME_IMPORT_ERR + "）")
        return sender

    inner = _wrap_with_bot_routing(sender)

    if not _outbox_enabled():
        log("outbox: 已禁用（DZMM_OUTBOX=off），直发")
        return inner
    try:
        db = os.environ.get("DZMM_OUTBOX_DB") or str(ROOT / "data" / "outbox.db")
        store = OutboxStore(db)
        wrapped = OutboxSender(inner, store)
        log("outbox: 已启用 db=" + db + " worker=" + wrapped.worker_id)
        return wrapped
    except Exception as e:                                 # noqa: BLE001
        log("outbox: 初始化失败，退回直发：" + str(e)[:100])
        return inner


async def main_loop(interval: float, dry: bool, once: bool):
    global _ACTIVE_CID, _captcha_active, _captcha_notified
    sender = _wrap_with_outbox(Sender(), dry)
    if not dry:
        _log_runtime_status()
        try:
            await sender.start()
        except Exception as e:
            log("sender init failed: " + str(e)[:80])

    state = load_state()
    log("start  dry=" + str(dry) + " interval=" + str(interval) + " groups=" + str(len(GROUPS)))

    captcha_cooldown_until = 0.0  # captcha 冷却截止时间戳
    http_backoff_until = 0.0  # 撞验证码后 HTTP 兜底退避截止时间戳（收发不受影响，继续）
    captcha_streak = 0  # 连续 captcha 次数（指数退避）
    _alerted_unauthorized = False  # unauthorized 告警防重（cookie 恢复后重置）
    _unauth_streak = 0  # 连续 unauthorized 次数（第 1 次即触发自动恢复）
    _recovering = False  # 是否正在自动恢复中
    group_meta = {cid: {"interval": FAST_INTERVAL, "last_poll": 0.0} for cid in GROUPS}

    # Socket.IO 订阅器（读消息主通道）：后台长连接监听 message:new，实时推送
    from sio_subscribe import SioSubscriber
    subscriber = SioSubscriber(GROUPS)
    sub_task = asyncio.create_task(subscriber.start())
    HTTP_FALLBACK_INTERVAL = 600  # HTTP 兜底对账间隔（秒）；断线补拉不受此限
    last_http_fallback = {cid: 0.0 for cid in GROUPS}
    _was_sio_connected = False  # 上一次 Socket.IO 连接状态（恢复检测用边沿：断→连）
    _last_scan_day = str(_scan_load().get("day") or "")  # 上次档案扫描日期（每天 06:00 一次）

    while True:
        # 本轮开始先清空人设：分组循环之外的所有 AI 调用都必须用默认人设。
        # 必须清！否则 contextvars 会残留上一轮最后一个群的人设（GROUPS 最后一个是 Rose 房间群），
        # 广播文案（问候/记忆汇报）就会被写成房间管家口吻。
        # —— 2026-09-17 事故：研讨群的早安变成了"（推开玫瑰房的窗…）Vv姐姐、糯糯姐，早上好"
        set_persona_cid("")
        # 检测 renew_token 连续失败信号，主动退出交给 entrypoint 重启
        if (ROOT / "token.emergency").exists():
            try:
                (ROOT / "token.emergency").unlink()
            except Exception:
                pass
            log("⚠️ 检测到 token.emergency 信号，主动退出交给 entrypoint 重启")
            return
        # 撞验证码恢复检测：Socket.IO 从「断→连」的边沿 = 风控解除，触发恢复
        # （用边沿而非单次检查，避免 Socket.IO 短暂波动导致时序竞态漏掉恢复）
        is_conn = subscriber.is_connected
        if _captcha_active and is_conn and not _was_sio_connected:
            _captcha_active = False
            _captcha_notified.clear()
            for _cid in GROUPS:
                if _cid in MUTE_CIDS:
                    continue
                await _send(sender, _cid, "🤖 " + CAPTCHA_RECOVERED_NOTICE, dry)
                await _rescreen_pending(sender, _cid, dry)
            log("✅ 风控解除（Socket.IO 已重连），已群内提醒 + 补筛查")
        _was_sio_connected = is_conn
        # 每天 06:00 后首次循环：全群扫描昵称/简介/签名变化，有变化就在群里说一声
        _today = time.strftime("%Y-%m-%d")
        if time.localtime().tm_hour >= PROFILE_SCAN_HOUR and _last_scan_day != _today:
            _last_scan_day = _today  # 先占位，避免扫描期间循环重入
            try:
                _notices = await asyncio.to_thread(_daily_profile_scan)
                for _ncid, _ntxt in _notices:
                    await _send(sender, _ncid, _ntxt, dry)
            except Exception as _e:
                log("档案扫描异常: " + str(_e)[:60])
        # 定时问候 / 记忆汇报：用主 sender（页面常驻、秒发）发出，不再靠独立进程的冷浏览器
        # （2026-09-16 事故：22:00 的晚安因独立进程冷启浏览器 + 平台抖动，整页加载超时全失败，
        #   认证群整晚没收到问候）
        try:
            await _scheduled_pushes(sender, dry)
        except Exception as _e:
            log("定时推送异常: " + str(_e)[:80])
        # 吹牛骰子超时跳过（3 分钟无动作 → 移到顺序最后，游戏继续）
        _dg = _dice_state()
        if (_dg.get("active") and _dg.get("phase") == "calling"
                and time.time() - _dg.get("last_action_ts", 0) > DICE_TIMEOUT_SEC):
            _dcur = _dice_current_player(_dg)
            if _dcur:
                _dmsg = _dice_skip_cmd(_dcur)
                _dcid = _dg.get("cid") or GROUPS[0]  # 广播只发发起群
                if _dmsg:
                    await _send(sender, _dcid, "🤖 " + _dmsg, dry)
                    log("⏱️ 骰子局超时跳过：" + get_name(_dcur))
        # 处理私聊消息（吹牛骰子 /看骰）
        try:
            _pmsgs = subscriber.drain_private()
            for _pcid, _plist in _pmsgs.items():
                for _m in _plist:
                    if is_self(_m.get("sent_by")):
                        continue
                    _ptext = ((_m.get("content") or {}).get("text") or "").strip()
                    if _ptext == "/看骰":
                        _puid = _m.get("sent_by", "")
                        _pg = _dice_state()
                        if _pg.get("active") and _puid in _pg.get("dice", {}):
                            await _dice_send_private(sender, _puid, "你的骰子：" + dice_str(_pg["dice"][_puid]))
                        else:
                            await _dice_send_private(sender, _puid, "你现在没有进行中的骰子局哦～发 /吹牛骰子 发起～")
        except Exception as _pe:
            log("私聊消息处理异常: " + str(_pe)[:80])
        captcha_hit = False
        for cid in GROUPS:
            _ACTIVE_CID = cid
            set_persona_cid(cid)  # 分群人设：本轮所有 AI 调用都按这个群的人设走
            meta = group_meta[cid]
            # 该群没到轮询时间，跳过（每群独立退避）
            if time.time() - meta["last_poll"] < meta["interval"]:
                continue
            meta["last_poll"] = time.time()
            had_new = False  # 本轮该群有没有新的用户消息
            old_last_id = None  # 本轮指令/@ 消息处理前的 last_id（生成失败回滚用）
            try:
                # 读消息：Socket.IO 推送优先，HTTP 低频兜底（对账补漏）
                sio_msgs = subscriber.drain(cid)
                need_resync = subscriber.need_resync(cid)
                now_ts = time.time()
                from_sio = False
                if sio_msgs:
                    msgs = sio_msgs
                    from_sio = True
                elif (need_resync or (now_ts - last_http_fallback.get(cid, 0) >= HTTP_FALLBACK_INTERVAL)) and now_ts >= http_backoff_until:
                    last_http_fallback[cid] = now_ts
                    if need_resync:
                        subscriber.clear_resync(cid)
                    msgs = await asyncio.to_thread(get_messages, cid, 20)
                    # HTTP 兜底成功 = 风控解除：清撞验证码状态 + 所有群提醒 + 补筛查延迟的新成员
                    if _captcha_active:
                        _captcha_active = False
                        _captcha_notified.clear()
                        for _cid in GROUPS:
                            if _cid in MUTE_CIDS:
                                continue
                            await _send(sender, _cid, "🤖 " + CAPTCHA_RECOVERED_NOTICE, dry)
                            await _rescreen_pending(sender, _cid, dry)
                        log("✅ 风控解除，已群内提醒 + 补筛查")
                else:
                    msgs = []
                if msgs:
                    group_state = state.get(cid) or {}
                    last_id = group_state.get("last_id")
                    # 定位 pending：Socket.IO 推送直接去重，HTTP 兜底用 last_id 定位
                    if from_sio:
                        pending = [m for m in msgs if m.get("message_id") != last_id]
                    else:
                        if last_id:
                            idx = -1
                            for i, m in enumerate(msgs):
                                if m.get("message_id") == last_id:
                                    idx = i
                                    break
                            if idx >= 0:
                                pending = msgs[idx + 1:]
                            else:
                                # last_id 已滚出窗口，只处理最新一条
                                pending = [msgs[-1]]
                        else:
                            # 首次启动，只处理最新一条，避免扫历史
                            pending = [msgs[-1]]
                    if not pending:
                        continue

                    # 静默监控群：只推进游标、不做任何处理（读得到但一声不吭）
                    if cid in MUTE_CIDS:
                        group_state["last_id"] = pending[-1]["message_id"]
                        state[cid] = group_state
                        save_state(state)
                        continue

                    # 本轮是否有真正的用户新消息（非 agent）
                    had_new = any(not is_self(m.get("sent_by")) for m in pending)

                    # 发言统计（活跃度 + 待评定发情缓冲 + 最近发言时间，每群独立持久化）
                    counts = group_state.get("msg_count") or {}
                    pending_buf = group_state.get("heat_pending") or {}
                    last_speak = group_state.get("last_speak") or {}
                    prev_speak = group_state.get("prev_speak") or {}  # 「上一条发言」时间（回顾功能的起点）
                    _now = time.time()
                    for m in pending:
                        uid = m.get("sent_by", "")
                        text = (m.get("content") or {}).get("text", "").strip()
                        if uid and not is_self(uid) and text and "加入了群聊" not in text and not text.startswith("/"):
                            if last_speak.get(uid):  # 先把「现在这条之前」的发言时间存下来
                                prev_speak[uid] = last_speak[uid]
                            counts[uid] = counts.get(uid, 0) + 1
                            last_speak[uid] = _now
                            if cid not in ROOM_CIDS:  # 房间群不攒发情值
                                buf = pending_buf.get(uid, [])
                                buf.append(text)
                                if len(buf) > 10:
                                    buf = buf[-10:]
                                pending_buf[uid] = buf
                    group_state["msg_count"] = counts
                    group_state["heat_pending"] = pending_buf
                    group_state["last_speak"] = last_speak
                    group_state["prev_speak"] = prev_speak
                    if had_new:
                        # 记录最后一条用户消息时间，立即落盘避免末尾 load_state 覆盖
                        group_state["last_msg_time"] = time.time()
                        state[cid] = group_state
                        save_state(state)

                    # 检测新成员加入：认证群做筛查（踢游客/踢男/提醒未填性别），
                    # 其他群只发中性欢迎语，不踢人、不发认证群文案
                    if not _get_story_state(cid).get("active"):
                        joins = _detect_joins(pending)
                        if joins:
                            if cid in SCREEN_CIDS:
                                log("🎉 [" + cid[:8] + "] 新成员加入 " + str(len(joins)) + " 人，开始筛查")
                                passed = await _handle_joins(sender, cid, joins, dry)
                                wtxt = WELCOME_TEXT
                            elif cid in ROOM_CIDS:
                                # 房间群：主动问好 + 屋主规矩 + 花瓣变出招待品
                                # （女鬼期间屋子没人，不迎客）
                                if (group_state.get("rose_ghost") or {}).get("active"):
                                    log("👻 [" + cid[:8] + "] 有客人进房，但管家不在（女鬼期间），不迎客")
                                    passed, wtxt = [], ""
                                else:
                                    log("🌹 [" + cid[:8] + "] 有客人进房 " + str(len(joins)) + " 人，上茶")
                                    for jid in joins:
                                        await _send(sender, cid,
                                                    "🤖 " + _room_greet_text(get_full_name(jid)), dry)
                                    passed, wtxt = [], ""
                            else:
                                log("🎉 [" + cid[:8] + "] 新成员加入 " + str(len(joins)) + " 人，只发欢迎语")
                                passed = joins
                                wtxt = WELCOME_TEXT_PLAIN
                            for jid in passed:
                                jname = get_full_name(jid)
                                await _send(sender, cid, "🤖 " + wtxt.replace("{name}", jname), dry)

                    # 检查 pending 里有没有 / 指令或 @ 消息
                    cmd = ""
                    cmd_sender = ""
                    at_msg = ""
                    at_sender = ""
                    room_hit = None  # (uid, 原文) 需要管家回应的房间消息
                    for m in pending:
                        if is_self(m.get("sent_by")):
                            continue  # 跳过 bot 自己的消息，避免 self-echo
                        t = (m.get("content") or {}).get("text", "").strip()
                        if t.startswith("/"):
                            cmd = t
                            cmd_sender = m.get("sent_by", "")
                            break
                        if not at_msg and is_at_me(t):
                            at_msg = t
                            at_sender = m.get("sent_by", "")
                        if not room_hit and _is_room_trigger(cid, m.get("sent_by", ""), t):
                            room_hit = (m.get("sent_by", ""), t)

                    # 房间群：客人发 / 指令 → 变成一次"婉拒"的房间回应（只有主人能下命令）
                    if cmd and cid in ROOM_CIDS and not _room_owner(cid, cmd_sender):
                        room_hit = (cmd_sender, cmd)
                        cmd = ""
                        at_msg = ""

                    if cmd:
                        reply = ""
                        log("⚡ [" + cid[:8] + "] " + cmd[:40])
                        old_last_id = group_state.get("last_id", "")  # 记录旧值，生成失败时回滚
                        # 先推进 last_id，避免处理中异常导致同一条指令被重复处理
                        group_state["last_id"] = msgs[-1]["message_id"]
                        state[cid] = group_state
                        save_state(state)

                        perf = _get_perf_state(cid)
                        perf_active = perf.get("active")

                        # 吹牛骰子命令（优先）
                        if cmd == "/吹牛骰子":
                            reply = await _dice_start_cmd(cid, cmd_sender)
                        elif cmd == "/加入":
                            reply = await _dice_join_cmd(cmd_sender)
                        elif cmd == "/牌局":
                            reply = _dice_status_text(_dice_state())
                        elif cmd == "/看骰":
                            # 群里发 /看骰，bot 私聊回骰子（不暴露给群里其他人）
                            _dg = _dice_state()
                            if _dg.get("active") and cmd_sender in _dg.get("dice", {}):
                                await _dice_send_private(sender, cmd_sender, "你的骰子：" + dice_str(_dg["dice"][cmd_sender]))
                                reply = "🎲 骰子已私聊发你啦～去看私聊哦～"
                            else:
                                reply = "你现在没有进行中的骰子局哦～"
                        elif cmd == "/开始":
                            reply = await _dice_begin_cmd(sender, cmd_sender)
                        elif cmd == "/下一轮" and _dice_state().get("phase") == "round_end":
                            reply = await _dice_next_round_cmd(sender)
                        elif cmd == "/继续" and _dice_state().get("phase") == "round_end" and not _get_fasao_state(cid).get("active"):
                            reply = await _dice_next_round_cmd(sender)
                        elif cmd == "/开":
                            reply = await _dice_open_cmd(cmd_sender)
                        elif cmd == "/退出" and _dice_state().get("active"):
                            reply = _dice_exit_cmd(cmd_sender)
                        elif cmd == "/结束" and _dice_state().get("active"):
                            reply = await _dice_quit_cmd(cmd_sender)
                        elif (dc := parse_call(cmd.lstrip("/"))) is not None:
                            reply = await _dice_call_cmd(cmd_sender, dc)
                        elif (_dice_state().get("active") and _dice_state().get("phase") == "calling"
                              and cmd.startswith("/") and re.match(r"^/\d", cmd)):
                            reply = "叫数格式要写成 /N个X 哦～比如 /3个3、/5个6（N 是数量，X 是 1~6 的点数）～"
                        # 公演结束
                        elif is_perf_end_cmd(cmd) and perf_active:
                            # 生成小说要跑十几次 pro 模型调用（很慢），放后台跑——
                            # 主循环是单协程串行处理所有群的，堵在这里会让 5 个群一起失联
                            # （2026-09-14 实测被堵 37 分钟以上）
                            asyncio.create_task(_perf_end_bg(sender, cid, dry))
                            reply = None
                        # 审核命令（仅糯糯，且待审核状态）
                        elif (rv := is_review_cmd(cmd)) is not None and cmd_sender == OWNER_UID and perf.get("review_status") == "pending":
                            await _handle_review(sender, cid, rv, dry)
                        # 公演开始
                        elif is_perf_start_cmd(cmd):
                            if _get_dj_state(cid).get("active"):
                                await _perf_send(sender, "调教进行中哦～先 /调教结束 再开公演～", cid, dry)
                            elif perf_active:
                                await _perf_send(sender, "公演已经进行中啦～", cid, dry)
                            else:
                                await _handle_perf_start(sender, cid, msgs[-1]["message_id"], dry)
                            reply = None  # 已自己发过消息了；空串会被当成"生成失败"回滚重试（2026-09-21 修）
                        # 公演进行中，其他指令不响应（交给互动逻辑）
                        elif perf_active:
                            reply = "🎭 公演进行中哦～暂无法响应其他指令，想参与就直接发言，等公演结束后再试～"
                        # 剧情结束（提前结束 → 直接进结局）
                        elif is_story_end_cmd(cmd) and _get_story_state(cid).get("active"):
                            reply = await asyncio.to_thread(_finish_story, cid)
                        # 投票结束（/投票结束 或停止词，投票进行中）
                        elif is_vote_end_cmd(cmd) and _get_vote_state(cid).get("active"):
                            reply = await asyncio.to_thread(_finish_vote, cid)
                        # 选择指令（投票优先，剧情次之；静默记录）
                        elif (choice := is_vote_choice_cmd(cmd)) is not None:
                            if _get_vote_state(cid).get("active"):
                                _record_vote(cid, cmd_sender, choice)
                                reply = None
                            elif _get_story_state(cid).get("active"):
                                if choice in ("1", "2", "3"):
                                    _record_story_vote(cid, cmd_sender, choice)
                                reply = None
                            else:
                                reply = p("no_vote", cid)
                        elif is_fasao_end_cmd(cmd) and _get_fasao_state(cid).get("active"):
                            # 发骚结束（停止词，发骚进行中）
                            reply = await asyncio.to_thread(end_fasao, cid)
                        elif is_dj_end_cmd(cmd):
                            # 调教结束
                            dj = _get_dj_state(cid)
                            if dj.get("active"):
                                end_text = _dj_end_text(dj.get("target_uid"), dj)
                                reply = "🔗 调教结束啦～"
                                if end_text:
                                    reply += "\n" + end_text
                                _set_dj_state(cid, {"active": False})
                            else:
                                # 通用停止词（/结束 /停止 /停下 /终止）用通用提示；/调教结束 明确是调教
                                if cmd.startswith("/调教结束"):
                                    reply = p("no_dial", cid)
                                else:
                                    reply = p("no_play", cid)
                        elif cmd.startswith("/ "):
                            # 斜杠后紧跟空格（如 / 清歌），格式错误
                            reply = p("slash_space", cid)
                        elif (dj_cmd := is_dj_command_cmd(cmd)) is not None:
                            # 调教命令（调教中才有效）
                            dj = _get_dj_state(cid)
                            if dj.get("active"):
                                if not dj_cmd:
                                    reply = "命令不能为空哦～格式 /命令 xxx"
                                else:
                                    dj["phase"] = "等回应"
                                    dj["last_activity"] = time.time()
                                    dj["round"] = dj.get("round", 0) + 1
                                    _set_dj_state(cid, dj)
                                    reply = await asyncio.to_thread(generate_diaojiao, _dj_target_text(dj["target_uid"]), dj_cmd)
                                    reply = (reply or "").strip()
                                    if reply:
                                        reply += "\n\n" + DJ_PARTICIPATE_HINT
                            else:
                                reply = p("unknown_cmd", cid)
                        elif is_diaojiao_cmd(cmd) is not None:
                            # 发起调教
                            if _get_dj_state(cid).get("active"):
                                reply = "调教进行中哦～先 /调教结束 再发起新的～"
                            else:
                                reply = await asyncio.to_thread(start_diaojiao, cid, cmd, cmd_sender)
                        elif _get_dj_state(cid).get("active"):
                            # 调教进行中，其他指令屏蔽
                            reply = "🔗 调教进行中哦～想参与就发 /命令 xxx，或 /调教结束 收尾～"
                        elif is_continue_cmd(cmd):
                            # 续写（发骚续写优先，continue_last 内部分发发骚/羞辱）
                            reply = await asyncio.to_thread(continue_last, cid, cmd, cmd_sender)
                        elif is_rewrite_cmd(cmd) is not None:
                            # 重写上一段（发骚 active 时有效）
                            reply = await asyncio.to_thread(rewrite_last, cid, cmd)
                        elif _get_fasao_state(cid).get("active"):
                            # 发骚进行中，其他指令屏蔽
                            reply = "🎲 发骚过程中暂时无法响应其他指令哦～请等结束后再试～想继续就发 /继续，想结束就发 /结束～"
                        elif is_story_start_cmd(cmd):
                            # 发起剧情游戏
                            if _get_story_state(cid).get("active"):
                                reply = "剧情已经进行中啦～"
                            else:
                                reply = await asyncio.to_thread(start_story, cid, cmd, cmd_sender)
                        elif is_vote_start_cmd(cmd):
                            # 发起投票（轻量，可与其他功能并存）
                            if _get_vote_state(cid).get("active"):
                                reply = "已经有进行中的投票啦～想结束发 /投票结束～"
                            else:
                                reply = await asyncio.to_thread(start_vote, cid, cmd, cmd_sender)
                        elif _get_story_state(cid).get("active"):
                            # 剧情进行中，其他指令屏蔽
                            reply = "🎬 剧情进行中哦～想投票就发 /选 1/2/3 或 /1/2/3，或 /剧情结束 收尾～"
                        elif _get_vote_state(cid).get("active"):
                            # 投票进行中，其他指令屏蔽
                            reply = "📊 投票进行中哦～想参与就发 /选 X 或 /X（X=序号），想结束发 /投票结束～"
                        elif is_help_cmd(cmd) or is_guide_cmd(cmd):
                            reply = p("help_text", cid) or HELP_TEXT
                        elif (mc := is_remember_cmd(cmd)) is not None:
                            # 记忆：/记住 内容
                            if not mc:
                                reply = p("mem_ask2", cid)
                            else:
                                _remember(cid, mc, get_name(cmd_sender))
                                reply = p("mem_saved", cid)
                        elif is_recall_cmd(cmd):
                            # 回忆：直接列出具体记忆内容（原文列表）
                            mem = _get_memory(cid)
                            if not mem:
                                reply = p("mem_empty", cid)
                            else:
                                total = len(mem)
                                shown = mem[-8:]
                                lines = [p("mem_list_head", cid).format(n=total)]
                                for i, m in enumerate(shown, 1):
                                    at_short = (m.get('at') or '')
                                    if len(at_short) >= 11:
                                        at_short = at_short[5:]
                                    lines.append(f"{i}. {m['text']}（{m.get('from', '?')}，{at_short}）")
                                if total > len(shown):
                                    lines.append(p("mem_list_more", cid).format(k=len(shown)))
                                reply = "\n".join(lines)
                        elif (devil_sw := is_devil_cmd(cmd)) is not None:
                            # 主动吐槽开关（恶魔+冷场共用，全局配置）
                            group_state["devil_enabled"] = (devil_sw == "on")
                            state[cid] = group_state
                            save_state(state)
                            reply = ("💬 主动吐槽已开启～我会偶尔冒个泡哦～"
                                     if devil_sw == "on" else "💬 主动吐槽已关闭～我先安静啦")
                        elif is_intro_cmd(cmd):
                            reply = p("intro_text", cid) or INTRO_TEXT
                        elif is_sync_cmd(cmd):
                            # 手动同步（仅糯糯）
                            if cmd_sender != OWNER_UID:
                                reply = p("sync_only_owner", cid)
                            else:
                                if not dry:
                                    await sender.send("🤖 收到同步指令～正在拉取增量消息，稍等一下下哦～", cid)
                                reply = await asyncio.to_thread(sync_profiles)
                        elif (reg_query := is_register_cmd(cmd)) is not None:
                            # 注册：先发提醒，再执行，最后发结果
                            if not dry:
                                target = reg_query or get_name(cmd_sender)
                                await sender.send(p("register_working", cid, who=target), cid)
                            reply = await asyncio.to_thread(register_user, reg_query, cmd_sender)
                        elif (query := is_profile_cmd(cmd)):
                            # 查人：先提醒，再执行
                            if not dry:
                                await sender.send(p("query_working", cid, who=query), cid)
                            text, registered = await asyncio.to_thread(archive_query, query)
                            if not text:
                                reply = p("not_found", cid)
                            elif registered:
                                text = _append_memory_ref(text, cid, query)
                                reply = await asyncio.to_thread(generate_archive, text)
                            else:
                                text = _append_memory_ref(text, cid, query)
                                reply = await asyncio.to_thread(generate_profile_summary, text)
                                reply += "\n（这个人还没注册哦，发 /注册 可以建立档案～）"
                        elif is_trend_board_cmd(cmd):
                            # 群运势榜（必须在 is_rank_cmd 之前，否则"运势排行"被骚度榜的"排行"吃掉）
                            reply = await week_trend_board(cid)
                            if not reply:
                                reply = p("unknown_cmd", cid)  # 非认证群：当成"不认识这个指令"
                        elif is_rank_cmd(cmd):
                            # 发情值排行
                            reply = await asyncio.to_thread(rank_summary)
                            if not reply:
                                reply = p("rank_empty", cid)
                        elif (rc_win := is_recap_cmd(cmd)) is not None:
                            # 「我不在的时候群里发生了什么」（仅认证群）
                            reply = await recap_absent(cid, cmd_sender, rc_win)
                            if not reply:
                                reply = p("unknown_cmd", cid)  # 非认证群：当成"不认识这个指令"
                        elif (tr_days := is_trend_cmd(cmd)) is not None:
                            # 过去 N 天运势趋势（必须排在 is_fortune_cmd 之前，否则 /运势趋势 会被 /运势 吃掉）
                            reply = await week_trend(cid, cmd_sender, tr_days)
                            if not reply:
                                reply = p("unknown_cmd", cid)  # 非认证群：当成"不认识这个指令"
                        elif is_summary_cmd(cmd):
                            # 总结类指令：先拉群聊记录，再让 AI 总结
                            chat_text = await asyncio.to_thread(summarize_chat, cmd)
                            group_name = get_group_name()
                            full_text = ("群名：" + group_name + "\n" + chat_text) if group_name else chat_text
                            reply = await asyncio.to_thread(generate_summary, full_text)
                        elif is_fortune_cmd(cmd):
                            # 算命：给发指令的人算今天运势
                            reply = await asyncio.to_thread(fortune, cmd_sender)
                        elif is_scene_cmd(cmd):
                            # 涩涩骰子：人物×场景×体位×附加玩法
                            reply = await asyncio.to_thread(scene, cmd)
                        elif is_poem_cmd(cmd) is not None:
                            # 写诗：随机文学方向，根据档案+发言写短诗
                            reply = await asyncio.to_thread(poem, cmd, cmd_sender)
                        else:
                            # 羞辱指令（返回 None 表示不是羞辱）
                            insult_target = is_insult_cmd(cmd)
                            if insult_target is not None:
                                target_uid, text, custom, found = await asyncio.to_thread(_insult_resolve, insult_target, cmd_sender)
                                if not found:
                                    reply = p("not_found_insult", cid)
                                else:
                                    # 被羞辱 → 发情值 +15
                                    _add_heat(cid, target_uid, HEAT_INSULT_TARGET)
                                    history = _get_insult_history(target_uid)
                                    text = _append_memory_ref(text, cid, get_full_name(target_uid))
                                    reply, style = await asyncio.to_thread(generate_insult, text, custom, history)
                                    _record_insult(target_uid, style, reply)
                                    # 记录上一段（供 /继续 续写）
                                    _set_last_action(cid, {
                                        "type": "insult",
                                        "uid": target_uid,
                                        "full": get_full_name(target_uid),
                                        "material": text,
                                        "output": reply,
                                    })
                            else:
                                reply = await asyncio.to_thread(generate_reply, [{"sender": "user", "text": cmd}])
                        if reply is not None:
                            reply = (reply or "").strip()
                            if reply:
                                reply = "🤖 " + reply
                                log("💬 " + reply[:200])
                                if dry:
                                    log("[dry] skip")
                                else:
                                    ok = await sender.send(reply, cid)
                                    log("✅" if ok else "❌")
                            else:
                                # 生成失败（空回复），抛异常让 except 统一回滚 last_id
                                raise RuntimeError("生成失败(空回复): " + cmd[:20])
                        # reply is None：静默成功（投票/剧情记录、公演屏蔽），不回复不报错

                    elif at_msg:
                        # 独占玩法进行中时，@ 提示进行中（打断保护）
                        active_hint = _active_play_hint(cid)
                        if active_hint:
                            reply = "🤖 " + active_hint
                            log("🚫 [" + cid[:8] + "] @消息被打断保护")
                            if not dry:
                                ok = await sender.send(reply, cid)
                                log("✅" if ok else "❌")
                        else:
                            # 被 @ 了（非公演中）
                            reply = ""
                            log("👋 [" + cid[:8] + "] @消息: " + at_msg[:40])
                            old_last_id = group_state.get("last_id", "")  # 记录旧值，生成失败时回滚
                            group_state["last_id"] = msgs[-1]["message_id"]
                            state[cid] = group_state
                            save_state(state)
                            q = _strip_at(at_msg)  # 去 @前缀，得到纯提问
                            mem_content = _extract_remember(q)
                            if mem_content is not None:
                                # 自然语言记忆请求：@小小糯 记住 XXX
                                if not mem_content:
                                    reply = p("mem_ask", cid)
                                else:
                                    _remember(cid, mem_content, get_name(at_sender))
                                    reply = p("mem_saved", cid)
                            elif _recap_intent(q):
                                # 大白话入口：@小小糯 我不在的时候群里发生了什么
                                reply = await recap_absent(cid, at_sender, q)
                                if not reply:
                                    reply = ""
                            else:
                                perf_q = _detect_perf_query(at_msg)
                                if perf_q:
                                    reply = await asyncio.to_thread(summarize_person_perf, perf_q[0], perf_q[1])
                                else:
                                    # 普通问答：精简上下文 + 注入记忆
                                    ctx = []
                                    for m in msgs[-4:]:
                                        muid = m.get("sent_by", "")
                                        if is_self(muid):
                                            continue
                                        mtxt = (m.get("content") or {}).get("text", "").strip()
                                        if mtxt:
                                            ctx.append({"sender": get_name(muid), "text": mtxt})
                                    glabel = _gender_label(at_sender)  # 女生/男生/未知
                                    ctx.append({"sender": get_name(at_sender) + ("（" + glabel + "）" if glabel != "未知" else ""), "text": q})
                                    mem = _retrieve_memory(cid, q, 10)
                                    if mem:
                                        mem_lines = "\n".join("- " + m["text"] for m in mem)
                                        ctx.insert(0, {"sender": "记忆", "text": "你之前记住的相关的事：\n" + mem_lines})
                                    reply = await asyncio.to_thread(generate_reply, ctx)
                            reply = (reply or "").strip()
                            if reply:
                                reply = "🤖 " + reply
                                log("💬 " + reply[:200])
                                if dry:
                                    log("[dry] skip")
                                else:
                                    ok = await sender.send(reply, cid)
                                    log("✅" if ok else "❌")
                            else:
                                # 生成失败（空回复），抛异常让 except 统一回滚 last_id
                                raise RuntimeError("生成失败(空回复): @消息")

                    elif room_hit:
                        # ============ 房间群：管家回应（被点名 / 主人指令 / 客人点单）============
                        r_uid, r_text = room_hit
                        cfg = ROOMS.get(cid) or {}
                        log("🌹 [" + cid[:8] + "] 房间消息: " + r_text[:40])
                        old_last_id = group_state.get("last_id", "")
                        group_state["last_id"] = msgs[-1]["message_id"]
                        state[cid] = group_state
                        save_state(state)
                        # ---- 女鬼状态：屋子里没人了（门和窗被带走），直到屋主回来 ----
                        ghost = group_state.get("rose_ghost") or {}
                        ghost_on = bool(ghost.get("active"))
                        if ghost_on and \
                                time.time() - float(ghost.get("ts") or 0) > ROOM_GHOST_MAX_H * 3600:
                            ghost_on = False  # 安全兜底：屋主太久没回来，自动收拾好（防房间永久哑掉）
                            group_state["rose_ghost"] = {"active": False, "ts": time.time()}
                            state[cid] = group_state
                            save_state(state)
                            log("👻 [" + cid[:8] + "] 女鬼状态超时，自动恢复")
                        if ghost_on and _room_owner(cid, r_uid):
                            # 屋主回来了 → 屋子复原
                            group_state["rose_ghost"] = {"active": False, "ts": time.time()}
                            state[cid] = group_state
                            save_state(state)
                            log("🌹 [" + cid[:8] + "] " + get_name(r_uid)[:12] + " 回来，女鬼退场")
                            reply = ROOM_GHOST_BACK.format(who=get_name(r_uid))
                        elif ghost_on:
                            # 女鬼还在：再摘花 → 警告+恐吓；其他话一律不接（小小糯在门外）
                            if _is_flower_grab(r_text) and \
                                    time.time() - float(group_state.get("rose_ghost_warn_ts") or 0) \
                                    >= ROOM_GHOST_WARN_COOLDOWN:
                                group_state["rose_ghost_warn_ts"] = time.time()
                                state[cid] = group_state
                                save_state(state)
                                log("👻 [" + cid[:8] + "] 女鬼期间又摘花，警告+恐吓")
                                reply = ROOM_GHOST_WARN
                            else:
                                reply = None  # 静默：管家不在，门也没有
                        elif r_text.startswith("/") and not _room_owner(cid, r_uid):
                            reply = ROOM_GUEST_REFUSE  # 客人用指令 → 固定话术婉拒，不惊动 AI
                        elif (_is_flower_grab(r_text)
                              and await asyncio.to_thread(_room_male, cid, r_uid)
                              and time.time() - float(group_state.get("rose_ghost_ts") or 0)
                              >= ROOM_GHOST_COOLDOWN):
                            # 男生摘花 → 放出红衣女鬼（固定剧本，不走 AI）
                            group_state["rose_ghost_ts"] = time.time()
                            group_state["rose_ghost"] = {"active": True, "ts": time.time()}
                            state[cid] = group_state
                            save_state(state)
                            log("👻 [" + cid[:8] + "] " + get_name(r_uid)[:12] + " 摘花，放出红衣女鬼")
                            reply = ROOM_GHOST_TEXT
                        elif (_sr := await _sign_reply(
                                cid, r_uid, r_text,
                                "Vv姐姐" if r_uid == VV_UID else
                                ("糯糯姐" if r_uid == NUONUO_UID else "客人"))):
                            # 星座运势（待客服务）：确定性算分 + 管家口吻点评
                            log("🔮 [" + cid[:8] + "] 星座: " + r_text[:30])
                            reply = _sr
                        else:
                            n = _strike_get(group_state, r_uid, int(cfg.get("strike_reset_h") or 12))
                            ctx = []
                            for m in msgs[-5:]:
                                muid = m.get("sent_by", "")
                                if is_self(muid):
                                    continue
                                mtxt = (m.get("content") or {}).get("text", "").strip()
                                if mtxt:
                                    ctx.append({"sender": get_name(muid), "text": mtxt})
                            ctx.insert(0, {"sender": "系统", "text":
                                           "（正在跟你说话的这位是「" + get_name(r_uid)
                                           + "」，"
                                           + ("是这间屋子的主人，要听 TA 的吩咐"
                                              if _room_owner(cid, r_uid) else "是一位来串门的客人")
                                           + "）"})
                            ctx.insert(0, {"sender": "系统", "text":
                                           "（如果这句话是在调戏你，就是 TA 的第 " + str(n + 1)
                                           + " 次，按第 " + str(n + 1)
                                           + " 档演出，并在回复最末尾加上 [[STRIKE]]）"})
                            reply = await asyncio.to_thread(generate_reply, ctx)
                            # 调戏计数：AI 的 [[STRIKE]] 标记为主，关键词兜底（必须点名了管家）
                            _hit = "[[STRIKE]]" in (reply or "")
                            if not _hit:
                                _named = ("小小糯" in r_text) or is_at_me(r_text)
                                _hit = _named and any(w in r_text for w in ROOM_LEWD_WORDS)
                                if _hit:
                                    log("🌹 [" + cid[:8] + "] 调戏（关键词兜底命中）")
                            if _hit:
                                nn = n + 1
                                if nn >= int(cfg.get("strike_limit") or 3):
                                    nn = 0  # 已送出门口，重新计数
                                    log("🌹 [" + cid[:8] + "] " + get_name(r_uid)[:12]
                                        + " 第三次调戏，送出去（纯台词）")
                                _strike_set(group_state, r_uid, nn)
                                state[cid] = group_state
                                save_state(state)
                        if reply is None:
                            pass  # 静默（女鬼期间管家不在，不接话）
                        else:
                            reply = reply.replace("[[STRIKE]]", "").strip()
                            if reply:
                                reply = "🤖 " + reply
                                log("💬 " + reply[:200])
                                if dry:
                                    log("[dry] skip")
                                else:
                                    ok = await sender.send(reply, cid)
                                    log("✅" if ok else "❌")
                            else:
                                raise RuntimeError("生成失败(空回复): 房间消息")

                    # 恶魔吐槽（无人@无人指令，普通聊天自主毒舌）
                    if not cmd and not at_msg and not room_hit:
                        roast = await asyncio.to_thread(_maybe_devil_roast, cid, msgs, group_state)
                        roast = (roast or "").strip()
                        if roast:
                            log("😈 [" + cid[:8] + "] 吐槽")
                            if dry:
                                log("[dry] " + roast[:50])
                            else:
                                ok = await sender.send("🤖 " + roast, cid)
                                log("✅" if ok else "❌")
                            # 立即落盘（devil 字段 + 本轮统计，避免末尾 load_state 覆盖）
                            state[cid] = group_state
                            save_state(state)

                    # 调教状态流转（目标回应 / 冷场接管 / 超时结束）
                    if _get_dj_state(cid).get("active"):
                        dj_reply = _check_diaojiao(cid, pending)
                        dj_reply = (dj_reply or "").strip()
                        if dj_reply:
                            log("🔗 [" + cid[:8] + "] 调教")
                            if dry:
                                log("[dry] " + dj_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + dj_reply, cid)
                                log("✅" if ok else "❌")

                    # 公演进行中的互动（丰富/羞辱/点评）
                    if _get_perf_state(cid).get("active"):
                        interact_reply = _check_perf_interact(cid, msgs)
                        interact_reply = (interact_reply or "").strip()
                        if interact_reply:
                            log("🎭 [" + cid[:8] + "] 互动")
                            if dry:
                                log("[dry] " + interact_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + interact_reply, cid)
                                log("✅" if ok else "❌")

                    # 剧情游戏状态流转（提醒投票 / 推进剧情 / 结局）
                    if _get_story_state(cid).get("active"):
                        story_reply = _check_story(cid)
                        story_reply = (story_reply or "").strip()
                        if story_reply:
                            log("🎬 [" + cid[:8] + "] 剧情")
                            if dry:
                                log("[dry] " + story_reply[:50])
                            else:
                                ok = await sender.send("🤖 " + story_reply, cid)
                                log("✅" if ok else "❌")

                    # 重新加载 state（scene/羞辱的 _add_heat 内部已改磁盘），避免内存旧 group_state 覆盖加分
                    state = load_state()
                    group_state = state.get(cid) or {}

                    # 推进 last_id 到最新（"只处理之后"的逻辑保证不会重复）
                    group_state["last_id"] = msgs[-1]["message_id"]
                    state[cid] = group_state
                    save_state(state)

                    # 批量评定发情值：30分钟周期；有发情值≥80的成员刚发言则立即评定（高潮卡在发言时刻）
                    hot_speaker = any(
                        (group_state.get("heat_value") or {}).get(m.get("sent_by", ""), 0) >= 80
                        for m in pending
                        if m.get("sent_by") and not is_self(m.get("sent_by"))
                        and (m.get("content") or {}).get("text", "").strip()
                        and "加入了群聊" not in (m.get("content") or {}).get("text", "")
                    )
                    if (group_state.get("heat_pending") or {}) and cid not in ROOM_CIDS and \
                            (time.time() - group_state.get("heat_last_judge", 0) >= 1800 or hot_speaker):
                        try:
                            await asyncio.to_thread(_judge_pending, cid)
                        except Exception as e:
                            log("judge err: " + str(e)[:60])
                        # 重新加载（_judge_pending 内部已写回 state.json）
                        state = load_state()
                        group_state = state.get(cid) or {}

                    # 高潮检测：发情值≥100 → 高潮事件 + 发布排名（剧情进行中跳过，避免刷屏）
                    if _get_story_state(cid).get("active") or cid in ROOM_CIDS:
                        hits, climax_changed = [], False
                    else:
                        hits, climax_changed = _climax_hits(group_state)
                    if climax_changed:
                        state[cid] = group_state
                        save_state(state)
                    if hits:
                        await _handle_climax(sender, cid, dry, hits)
                        await _publish_rank(sender, cid, dry)
                    else:
                        # 定时发布：每天 12:00 和 18:00
                        now = datetime.now()
                        today = now.strftime("%Y-%m-%d")
                        has_data = bool((group_state.get("heat_value") or {}) or (group_state.get("msg_count") or {}))
                        if (now.hour in (12, 18) and cid in PUSH_CIDS and group_state.get("last_daily_rank_date") != today
                                and has_data):
                            group_state["last_daily_rank_date"] = today
                            state[cid] = group_state
                            save_state(state)
                            await _publish_rank(sender, cid, dry)

                # 冷场吐槽（群里冷清，时间驱动，无新消息也执行）——房间群不毒舌
                cold_reply = "" if cid in ROOM_CIDS else await asyncio.to_thread(_maybe_devil_cold_roast, cid)
                cold_reply = (cold_reply or "").strip()
                if cold_reply:
                    log("😈 [冷场] [" + cid[:8] + "] 吐槽")
                    if dry:
                        log("[dry] " + cold_reply[:50])
                    else:
                        ok = await sender.send("🤖 " + cold_reply, cid)
                        log("✅" if ok else "❌")

                # 发骚超时自动结束（防卡死：发骚 15 分钟无互动自动解锁）
                if _get_fasao_state(cid).get("active"):
                    fasao_timeout = _check_fasao_timeout(cid)
                    fasao_timeout = (fasao_timeout or "").strip()
                    if fasao_timeout:
                        log("⏱️ [" + cid[:8] + "] 发骚超时自动结束")
                        if dry:
                            log("[dry] " + fasao_timeout[:50])
                        else:
                            ok = await sender.send("🤖 " + fasao_timeout, cid)
                            log("✅" if ok else "❌")

                # 投票定时检查（截止到→自动结束；时间驱动，无新消息也执行）
                if _get_vote_state(cid).get("active"):
                    vote_reply = _check_vote(cid)
                    vote_reply = (vote_reply or "").strip()
                    if vote_reply:
                        log("📊 [" + cid[:8] + "] 投票")
                        if dry:
                            log("[dry] " + vote_reply[:50])
                        else:
                            ok = await sender.send("🤖 " + vote_reply, cid)
                            log("✅" if ok else "❌")
            except Exception as e:
                msg = str(e)
                if "captcha" in msg.lower():
                    # 撞验证码：只降级 HTTP 功能，不退出（收发走 /ws + Playwright 不受影响）
                    http_backoff_until = time.time() + 3600  # HTTP 兜底退避 1 小时
                    _captcha_active = True
                    if not _captcha_notified.get(cid):
                        _captcha_notified[cid] = True
                        await _send(sender, cid, "🤖 " + CAPTCHA_NOTICE, dry)
                        # 拉 noVNC 人工验证窗口（撞验证码时提供人工过滑块入口，只拉一次防刷屏）
                        try:
                            subprocess.Popen(
                                [HERMES_PY, "captcha_novnc.py"],
                                cwd=str(ROOT), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
                            log("已拉起 captcha_novnc.py（noVNC 人工验证）")
                        except Exception as _e:
                            log("拉起 captcha_novnc 失败: " + str(_e)[:60])
                    log("⚠️ 撞验证码，降级 HTTP 功能（收发继续），HTTP 兜底退避 1 小时")
                    # 不写 bot.paused、不 return，继续循环处理 Socket.IO 实时推送
                if "unauthorized" in msg.lower() or "invalid authentication" in msg.lower():
                    captcha_hit = True
                    captcha_cooldown_until = time.time() + 1800  # cookie 失效，冷却 30 分钟（避免频繁失败触发 IP 风控）
                    _unauth_streak += 1
                    log("⚠️ UNAUTHORIZED（cookie 可能失效），冷却 30 分钟（连续第 " + str(_unauth_streak) + " 次）")
                    # 事件驱动告警：cookie 失效发微信（防重，cookie 恢复后重置）
                    if not _alerted_unauthorized:
                        _alerted_unauthorized = True
                        try:
                            ok = await asyncio.to_thread(send_weixin, "🚨【小小糯bot】DZMM cookie 失效（unauthorized），已自动触发恢复，无需手动操作")
                            log("📱 unauthorized 告警: " + ("✅ 已发微信" if ok else "❌ 发送失败（代理可能不稳）"))
                        except Exception as _e:
                            log("📱 unauthorized 告警异常: " + str(_e)[:60])
                    # 第 1 次 unauthorized 就触发自动登录恢复（headless 兜底锁屏也能成功，不必等 2 次）
                    if _unauth_streak >= 1 and not _recovering:
                        _recovering = True
                        log("🔄 连续 " + str(_unauth_streak) + " 次 unauthorized，触发自动登录恢复")
                        try:
                            subprocess.Popen(
                                [HERMES_PY, "auto_recover.py"],
                                cwd=str(ROOT),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True,
                            )
                        except Exception as _e:
                            log("🔄 自动恢复触发失败: " + str(_e)[:60])
                    break
                # 生成失败（网络超时等），回滚 last_id，下轮重试
                if old_last_id is not None:
                    try:
                        _rollback_last_id(cid, old_last_id)
                        log("⚠️ [" + cid[:8] + "] 生成异常，回滚 last_id，下轮重试")
                    except Exception:
                        pass
                log("loop[" + cid[:8] + "]: " + msg[:100])

            # Socket.IO 订阅是实时推送，drain 为纯内存操作，固定最快间隔（不退避）；
            # HTTP 兜底已由 HTTP_FALLBACK_INTERVAL 独立低频控制，无需在此退避
            meta["interval"] = FAST_INTERVAL
        if once:
            break
        # captcha 降频：触发后长冷却，冷却结束且恢复后才重置计数
        if captcha_hit or time.time() < captcha_cooldown_until:
            wait = max(captcha_cooldown_until - time.time(), 1.0)
        else:
            captcha_streak = 0  # 恢复正常轮询，重置连续 captcha 计数
            _alerted_unauthorized = False  # 恢复正常，重置告警防重标志
            _unauth_streak = 0  # 恢复正常，重置连续 unauthorized 计数
            _recovering = False  # 重置自动恢复标志
            wait = 0.2  # 小睡，每群按自己的 interval 判断轮询时间
        await asyncio.sleep(wait)

    await sender.close()



# ---------------------------------------------------------------- 房间群（独立人设 + 屋主权限）

_ROOM_LAYOUT_WORDS = ["改变房间布局", "换房间布局", "改变布局", "换个布局", "重新布置",
                      "布置房间", "布置一下", "换个风格", "改变风格", "装修", "改造房间",
                      "换布置", "重新装修", "改一下房间", "变一下房间"]
_ROOM_CONJURE_WORDS = ["给我", "拿来", "变个", "变出", "我要", "帮我拿", "帮我变",
                       "端来", "来一个"]


def _room_owner(cid: str, uid: str) -> bool:
    """是不是这间屋子的主人（屋主 + 操作者）——只有他们能下命令."""
    cfg = ROOMS.get(cid) or {}
    return uid in (cfg.get("owners") or set())


def _is_room_trigger(cid: str, uid: str, text: str) -> bool:
    """房间群：这条消息要不要管家回应（被点名 / 主人指令 / 客人点单）."""
    cfg = ROOMS.get(cid)
    if not cfg or not text or is_self(uid):
        return False
    t = text.strip()
    if not t or t.startswith("/"):
        return False  # / 指令走权限分支
    if ("小小糯" in t) or is_at_me(t):
        return True
    if _is_flower_grab(t):
        return True  # 摘花 → 进房间分支（男生放任女鬼，其他人由 AI 婉拒）
    if any(w in t for w in ROSE_SIGN_WORDS) or _sign_find(t):
        return True  # 星座运势（待客服务，客人也能用）
    if _sign_parse_birth(t) and any(k in t for k in ("我是", "生", "生日", "出生", "座")):
        return True  # 报生日
    if _room_owner(cid, uid):
        # 主人没点名也可能在下命令
        return (any(w in t for w in _ROOM_LAYOUT_WORDS)
                or any(w in t for w in _ROOM_CONJURE_WORDS))
    # 客人：只有明确的点单才回应（被点名 / 进群问好另外处理）
    return (any(v in t for v in ROOM_ORDER_VERBS)
            and any(w in t for w in ROOM_MENU_WORDS))


def _is_flower_grab(t: str) -> bool:
    """是不是在摘花：整句命中，或「花名 + 摘取动词」组合命中（抗语序变化）."""
    if not t:
        return False
    if any(w in t for w in ROOM_FLOWER_WORDS):
        return True
    return (any(n in t for n in ROOM_FLOWER_NOUNS)
            and any(v in t for v in ROOM_FLOWER_VERBS))


# ---------------------------------------------------------------- 星座运势（Rose 专属）

_SIGN_NAMES = [s[0] for s in ROSE_SIGNS]
# 一级：全称「X座」—— 任何时候都认（天平座是常见错写，带"座"所以安全）
_SIGN_FULL = {"白羊座": "白羊", "金牛座": "金牛", "双子座": "双子", "巨蟹座": "巨蟹",
              "狮子座": "狮子", "处女座": "处女", "天秤座": "天秤", "天蝎座": "天蝎",
              "射手座": "射手", "摩羯座": "摩羯", "水瓶座": "水瓶", "双鱼座": "双鱼",
              "天平座": "天秤"}
# 二级：简称 —— 只有同句出现星座意图词时才认
# （已删掉"牧羊/室女/人马/山羊/宝瓶"这些非标准别名：牧羊犬、人马、山羊本身就有歧义）
_SIGN_SHORT = {"白羊": "白羊", "金牛": "金牛", "双子": "双子", "巨蟹": "巨蟹",
               "狮子": "狮子", "处女": "处女", "天秤": "天秤", "天蝎": "天蝎",
               "射手": "射手", "摩羯": "摩羯", "水瓶": "水瓶", "双鱼": "双鱼"}
# 句首「我+座名」也算自报星座（"我天秤"、"我水瓶的"）
# 注意必须锚定句首：否则"我今天买了个水瓶"会被误判
_SIGN_ME_RE = re.compile(r"^\s*我\s*(" + "|".join(_SIGN_SHORT.keys()) + ")")


def _sign_from_md(month: int, day: int):
    """月-日 → 星座条目（含元素/守护星/关键词）; 非法日期返回 None."""
    try:
        month, day = int(month), int(day)
    except Exception:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    for name, el, ruler, (sm, sd), (em, ed), kw in ROSE_SIGNS:
        if (month, day) >= (sm, sd) and (month, day) <= (em, ed):
            return (name, el, ruler, kw)
        if sm > em:  # 跨年段（摩羯）
            if (month, day) >= (sm, sd) or (month, day) <= (em, ed):
                return (name, el, ruler, kw)
    return None


def _sign_intent(text: str) -> bool:
    """句子里有没有"星座意图"（用来自动消歧简称）."""
    return any(k in text for k in (
        "我是", "我 是", "本人", "我属于", "我太阳", "我月亮", "我上升",
        "什么座", "哪个座", "星座", "星象", "星盘", "运势", "运气",
        "怎么样", "如何", "看看", "查查", "查一下", "今天", "本周", "这周"))


def _sign_find(text: str):
    """认星座。分两级（2026-09-21 屋主要求：避免误触发）：
    一级：全称「X座」—— 任何时候都认（最安全）
    二级：简称（"天蝎"）—— 只有同句出现星座意图词时才认（"我是天蝎"、"天蝎怎么样"）
    所以光发「白羊」不再触发，但「我是白羊」照样认。
    """
    for full, std in _SIGN_FULL.items():
        if full in text:
            return std
    m = _SIGN_ME_RE.match(text)
    if m:
        return _SIGN_SHORT.get(m.group(1))
    if _sign_intent(text):
        for short, std in _SIGN_SHORT.items():
            if short in text:
                return std
    return None


def _sign_parse_birth(text: str):
    """从文本里解析生日（月-日），返回 (month, day) 或 None.
    支持：11月5号 / 11-5 / 11.5 / 11/5 / 5月11号（靠月<=12 判断顺序）."""
    m = re.search(r"(\d{1,2})\s*[月\-./]\s*(\d{1,2})", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 1 <= b <= 31:
            return (a, b)
        if 1 <= b <= 12 and 1 <= a <= 31:  # 写反了（日-月）
            return (b, a)
        return None
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})", text)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def _sign_stored(cid: str, uid: str):
    """读某人在这间屋子登记的生日 → (sign, month, day) 或 None（含平台生日兜底）."""
    gs = (load_state().get(cid) or {})
    d = (gs.get("rose_signs") or {}).get(uid) or {}
    if d.get("sign"):
        return (d["sign"], int(d.get("m") or 0), int(d.get("d") or 0))
    # 兜底：平台资料里万一填了生日
    try:
        u = get_user(cid, uid) or {}
        b = str(u.get("birthday") or "")
        if len(b) >= 10:
            mm, dd = int(b[5:7]), int(b[8:10])
            s = _sign_from_md(mm, dd)
            if s:
                return (s[0], mm, dd)
    except Exception:
        pass
    return None


def _sign_remember(cid: str, uid: str, sign: str, month: int, day: int) -> None:
    """把生日写进房间的册子（只记月-日，不记年份）."""
    try:
        st = load_state()
        gs = st.get(cid) or {}
        d = gs.get("rose_signs") or {}
        d[uid] = {"sign": sign, "m": int(month), "d": int(day), "at": time.time()}
        gs["rose_signs"] = d
        st[cid] = gs
        save_state(st)
    except Exception as e:
        log("星座记录失败: " + str(e)[:60])


def _sign_calc(uid: str, sign: str, date_str: str) -> dict:
    """确定性算分：同一个人、同一天、同一个星座 → 结果固定（和认证群五行运势一致）."""
    h = hashlib.md5((uid + "|" + date_str + "|" + sign).encode("utf-8")).hexdigest()
    nums = [int(h[i:i + 4], 16) for i in range(0, 24, 4)]
    dims = {
        "爱情": 52 + nums[0] % 44,
        "事业": 52 + nums[1] % 44,
        "财运": 52 + nums[2] % 44,
        "心情": 52 + nums[3] % 44,
    }
    luck = {
        "item": ROSE_LUCKY_ITEMS[nums[0] % len(ROSE_LUCKY_ITEMS)],
        "dir": ROSE_LUCKY_DIRS[nums[1] % len(ROSE_LUCKY_DIRS)],
        "color": ROSE_LUCKY_COLORS[nums[2] % len(ROSE_LUCKY_COLORS)],
        "yi": ROSE_SIGN_YI[nums[3] % len(ROSE_SIGN_YI)],
        "ji": ROSE_SIGN_JI[nums[4] % len(ROSE_SIGN_JI)],
    }
    # 月相按日期取（全房间一致，仅氛围）
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    luck["moon"] = ROSE_MOONS[(dt.timetuple().tm_yday // 4) % len(ROSE_MOONS)]
    return {"dims": dims, "luck": luck, "total": sum(dims.values()) // len(dims)}


def _sign_recent(uid: str, cid: str) -> str:
    """该客人最近的发言（给 AI 做个性化点评用）."""
    try:
        their = _get_msgs_by_uid(uid) or []
        return " / ".join(str(t).replace("\n", " ")[:40] for t in their[-4:])
    except Exception:
        return ""


async def _sign_card(cid: str, uid: str, sign: str, month: int, day: int,
                     who: str = "客人") -> str:
    """组装星座运势卡（固定 ≤10 行，AI 只写最后一段）."""
    info = None
    for n, el, ruler, kw in [(s[0], s[1], s[2], s[5]) for s in ROSE_SIGNS]:
        if n == sign:
            info = (el, ruler, kw)
            break
    if not info:
        return ""
    el, ruler, kw = info
    today = datetime.now().strftime("%Y-%m-%d")
    r = _sign_calc(uid, sign, today)
    d, lk = r["dims"], r["luck"]
    try:
        para = await asyncio.to_thread(
            generate_horoscope, sign, el, ruler, lk["moon"], d, lk["yi"], lk["ji"],
            today, who, ("最近发言：" + _sign_recent(uid, cid)) if _sign_recent(uid, cid) else "")
    except Exception as e:
        log("星座点评生成失败: " + str(e)[:60])
        para = ""
    para = " ".join(str(para or "").split())
    if not para:
        para = ("（抬手拂过窗边那枝玫瑰，花瓣上浮起细碎的光）"
                + "今天的星象不算平顺，把节奏放慢一点反而顺。"
                + "（把一杯温水推到你手边）宜" + lk["yi"] + "，忌" + lk["ji"] + "，其余交给时间。")
    md_txt = str(month).zfill(2) + "-" + str(day).zfill(2)
    return "\n".join([
        "🌹 今日星象 · " + sign + "座",
        "————————————",
        "🪐 " + el + "象 · 守护星" + ruler + " · 月相：" + lk["moon"] + " · " + md_txt + "生",
        "💗爱情" + str(d["爱情"]) + " · 💼事业" + str(d["事业"])
        + " · 💰财运" + str(d["财运"]) + " · 🌙心情" + str(d["心情"]),
        "🍀 幸运：" + lk["item"] + " · " + lk["dir"] + " · " + lk["color"],
        "📅 宜" + lk["yi"] + " ｜ 忌" + lk["ji"],
        "————————————",
        para,
    ])


async def _sign_reply(cid: str, uid: str, text: str, who: str = "客人") -> str:
    """星座相关消息的统一入口。返回要发的内容；不是星座话题则返回 None（交给 AI 正常回应）."""
    t = (text or "").strip()
    if not t:
        return None
    has_word = any(w in t for w in ROSE_SIGN_WORDS)
    named = _sign_find(t)
    birth = _sign_parse_birth(t)
    owner = _room_owner(cid, uid)

    # ① 报星座："我是天蝎座的"
    if named and (any(k in t for k in ("我是", "我 是", "我属于", "本人", "我太阳")) or not has_word):
        if any(k in t for k in ("我是", "我属于", "本人")):
            _sign_remember(cid, uid, named, 0, 0)
            card = await _sign_card(cid, uid, named, 0, 0, who)
            return "（把一片花瓣夹进册子）记下了，" + named + "座。\n" + card
    # ② 报生日："我 11 月 5 号生的" / "我是 3.15 出生的" / "生日 12-22"
    if birth and any(k in t for k in ("我是", "生", "生日", "出生", "座")):
        s = _sign_from_md(birth[0], birth[1])
        if s:
            _sign_remember(cid, uid, s[0], birth[0], birth[1])
            card = await _sign_card(cid, uid, s[0], birth[0], birth[1], who)
            return ("（翻开册子写下日子，花瓣在字上停了一下）"
                    + str(birth[0]) + "月" + str(birth[1]) + "日，是" + s[0] + "座。\n" + card)
        return "这个日子小小糯认不出来……月日再报一次？比如 11 月 5 号。"
    # ③ 问房间整体星象（屋主专属）
    if owner and any(k in t for k in ("房间星象", "今日星象", "今天适合", "屋子运势", "房间运势")):
        card = await _sign_card(cid, cid, "天秤", 9, 23, "Vv姐姐" if uid != NUONUO_UID else "糯糯姐")
        return "（把窗推开一条缝，夜里的香气涌进来）今天这间屋子——\n" + card
    # ④ 星象榜：谁运势最好
    if any(k in t for k in ("谁运势", "谁运气", "运势排行", "星象榜", "谁最好")):
        gs = (load_state().get(cid) or {})
        rec = gs.get("rose_signs") or {}
        if not rec:
            return "册子上还没有人报过生日呢，报一个，小小糯才好替你们看。"
        today = datetime.now().strftime("%Y-%m-%d")
        _load_member_names()  # 先刷新昵称缓存，免得榜上显示 uid 前缀
        rows = []
        for u, dv in rec.items():
            sg = dv.get("sign")
            if not sg:
                continue
            rows.append((_sign_calc(u, sg, today)["total"], get_name(u), sg))
        if not rows:
            return "册子上还没有人能算呢。"
        rows.sort(reverse=True)
        lines = ["🔮 今日星象榜"]
        for i, (sc, nm, sg) in enumerate(rows[:8], 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, str(i) + ".")
            lines.append(medal + " " + nm + " · " + sg + "座 · " + str(sc))
        return "\n".join(lines[:10])
    # ⑤ 问运势（指名某个星座 / 问自己的）
    #   注意：只要文本里出现星座名（哪怕光发"白羊座"三个字）也算——光发座名就当他来看这个座的运势
    if has_word or named:
        if named:  # 指名某个星座（不一定是自己）
            card = await _sign_card(cid, uid, named, 0, 0, who)
            _st = _sign_stored(cid, uid)
            if _st and _st[0] == named:
                return card
            # 没记录 / 记的不是这一座 → 末尾轻轻问一句要不要记进册子
            return card + "\n（若是你本人的座，说一句「我是" + named + "座」，小小糯替你记进册子。）"
        got = _sign_stored(cid, uid)
        if not got:
            return ("（合上册子）小小糯这儿还没记过你的生日呢。"
                    "报一个就好——几月几号，或者直接说你是哪个星座？")
        card = await _sign_card(cid, uid, got[0], got[1], got[2], who)
        return card
    return None


# ---------------------------------------------------------------- 「我不在的时候」回顾（仅认证群）

RECAP_CIDS = {PRIMARY_CID}          # 只在认证妹子群开放
RECAP_MAX_MSGS = 400                # 窗口内最多取多少条喂给 AI（防刷屏撑爆）
RECAP_MAX_HOURS = 48                # 窗口最长 48 小时
RECAP_COOLDOWN = 30                 # 同一时间窗重复问的冷却（换个时间段问不受限）


def is_recap_cmd(cmd: str):
    """识别 /我不在 /补课 /错过 指令，返回窗口描述（可为空串）；None = 不是."""
    for p in ["/我不在", "/补课", "/错过了什么", "/错过"]:
        if cmd.startswith(p):
            return cmd[len(p):].strip()
    return None


def _recap_intent(text: str) -> bool:
    """自然语言里是不是在问"我不在的时候发生了什么"（配合 @小小糯 用）."""
    t = text or ""
    if "我不在" in t or "错过" in t:
        return True
    if "发生" in t and any(k in t for k in ("什么", "啥")):
        return True
    if any(k in t for k in ("有啥事", "有什么瓜", "有瓜吗", "群里咋了", "群里怎么了")):
        return True
    return False


def _recap_window(cid: str, uid: str, text: str):
    """算出回顾起点，返回 (起点时间戳, 人类可读窗口描述)."""
    now = time.time()
    t = text or ""
    # 手动指定（支持阿拉伯数字和中文数字：3小时 / 两小时 / 半天）
    cn = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}
    m = re.search(r"(\d+)\s*(小时|钟头)", t)
    if m:
        h = min(int(m.group(1)), RECAP_MAX_HOURS)
        return (now - h * 3600, now, "最近 " + str(h) + " 小时")
    m = re.search(r"([一两二三四五六七八九十])\s*(小时|钟头)", t)
    if m:
        h = min(cn.get(m.group(1), 1), RECAP_MAX_HOURS)
        return (now - h * 3600, now, "最近 " + str(h) + " 小时")
    if "半天" in t:
        return (now - 12 * 3600, now, "最近半天")
    if "一天" in t or "24小时" in t:
        return (now - 24 * 3600, now, "最近一天")
    m = re.search(r"([一两二三四五六七八九十\d]+)\s*天(前|以前)", t)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else cn.get(raw, 1)
        d0 = _day0(datetime.now() - timedelta(days=n))
        return (d0, d0 + 86400, str(n) + " 天前")
    if "大前天" in t:
        d0 = _day0(datetime.now() - timedelta(days=3))
        return (d0, d0 + 86400, "大前天")
    if "前天" in t:
        d0 = _day0(datetime.now() - timedelta(days=2))
        return (d0, d0 + 86400, "前天")
    if "前几天" in t:
        return (_day0(datetime.now() - timedelta(days=3)), now, "前几天")
    # 周末（必须放在"上周"之前判断，否则"上周末"会被"上周"先吃掉）
    if any(k in t for k in ("周末", "礼拜六", "礼拜天", "礼拜日")):
        d = datetime.now()
        back = (d.weekday() - 5) % 7          # 最近的那个周六
        sat = _day0(d - timedelta(days=back))
        end = min(sat + 2 * 86400, now)       # 到周日 24:00（未结束则到现在）
        return (sat, end, "上周末" if back else "本周末")
    # 周X / 星期X / 上周X（"上周三"必须放在"上周"之前）
    m = re.search(r"(周|星期|礼拜)([一二三四五六日天])", t)
    if m:
        wmap = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        w = wmap.get(m.group(2))
        if w is not None:
            d = datetime.now()
            back = (d.weekday() - w) % 7
            d0 = _day0(d - timedelta(days=back))
            end = min(d0 + 86400, now)
            return (d0, end, "周" + m.group(2))
    if any(k in t for k in ("上周", "上个星期", "上一周", "上星期")):
        d = datetime.now()
        this_mon = _day0(d - timedelta(days=d.weekday()))
        return (this_mon - 7 * 86400, this_mon, "上周")
    if any(k in t for k in ("上个月", "上月")):
        first = _day0(datetime.now().replace(day=1))
        prev_month = datetime.fromtimestamp(first - 86400)  # 上个月最后一天
        return (_day0(prev_month.replace(day=1)), first, "上个月")
    if any(k in t for k in ("这个月", "本月")):
        return (_day0(datetime.now().replace(day=1)), now, "这个月")
    if "今天" in t or "今日" in t:
        d0 = _day0(datetime.now())
        if "早上" in t or "早晨" in t:
            return (d0, min(d0 + 9 * 3600, now), "今天早上")
        if "上午" in t:
            return (d0, min(d0 + 12 * 3600, now), "今天上午")
        if "中午" in t:
            return (d0 + 11 * 3600, min(d0 + 14 * 3600, now), "今天中午")
        if "下午" in t:
            return (d0 + 12 * 3600, min(d0 + 18 * 3600, now), "今天下午")
        if "晚上" in t:
            return (d0 + 18 * 3600, now, "今天晚上")
        return (d0, now, "今天")
    if "昨天" in t or "昨日" in t:
        d0 = _day0(datetime.now() - timedelta(days=1))
        return (d0, d0 + 86400, "昨天")
    if "上午" in t:
        return (_day0(datetime.now()), now, "今天上午起")
    if "下午" in t:
        return (_day0(datetime.now()) + 12 * 3600, now, "今天下午起")
    if "晚上" in t:
        return (_day0(datetime.now()) + 18 * 3600, now, "今晚起")
    # 默认：你上次发言之前的那一条
    gs = (load_state().get(cid) or {})
    prev = (gs.get("prev_speak") or {}).get(uid)
    last = (gs.get("last_speak") or {}).get(uid)
    since = prev or last
    if since and now - since <= RECAP_MAX_HOURS * 3600:
        return (since, now, "")
    return (now - 6 * 3600, now, "最近 6 小时")


def _day0(dt) -> float:
    """某天 00:00 的时间戳."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _msg_ts(m: dict) -> float:
    """消息时间戳（sent_at 是 UTC 字符串）."""
    try:
        return datetime.fromisoformat(
            str(m.get("sent_at") or "").replace("+00", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _recap_mentions(msgs: list, uid: str):
    """扫窗口内有没有人提到/@他。返回 (次数, 提到他的人名列表, 一句样例)."""
    names = {get_full_name(uid), get_name(uid)}
    names = {n for n in names if n and len(n) >= 2}
    if not names:
        return (0, [], "")
    hits, who, sample = 0, [], ""
    for m in msgs:
        if is_self(m.get("sent_by")) or m.get("sent_by") == uid:
            continue
        t = (m.get("content") or {}).get("text", "")
        if not t or "加入了群聊" in t:
            continue  # 入群提示里的名字不算"有人找你"（但仍保留在转录里给 AI 归纳）
        if any(("@" + n) in t or (n in t) or (n[:4] in t and len(n) >= 4) for n in names):
            hits += 1
            nm = get_name(m.get("sent_by", "")) or "有人"
            if nm not in who:
                who.append(nm)
            if not sample:
                sample = t[:60]
    return (hits, who[:3], sample)


def _recap_transcript(msgs: list) -> tuple:
    """消息列表 → 给 AI 的转录文本 + 条数（bot 消息标成"小小糯:"，长文截断）."""
    lines = []
    for m in msgs:
        u = m.get("sent_by", "")
        t = ((m.get("content") or {}).get("text") or "").strip()
        if not t:
            continue
        if is_self(u):
            lines.append("小小糯: " + t[:100])
        else:
            lines.append(get_name(u) + ": " + t[:80])
    return ("\n".join(lines), len(lines))


async def recap_absent(cid: str, uid: str, text: str = "") -> str:
    """「我不在的时候群里发生了什么」——返回 ≤10 行的回顾."""
    if cid not in RECAP_CIDS:
        return ""
    gs = (load_state().get(cid) or {})
    since, until, wlabel = _recap_window(cid, uid, text)
    wkey = str(int(since) // 60)  # 窗口指纹（起点取整到分钟）
    # 冷却只拦"同一时间窗重复问"；换个时间段问（昨天/上午/两小时）直接放行
    cd = gs.get("recap_ts") or {}
    last = cd.get(uid) or {}
    if isinstance(last, dict) and last.get("key") == wkey \
            and time.time() - float(last.get("ts") or 0) < RECAP_COOLDOWN:
        return "这一段刚给你说完呢～换个时间段问（比如「昨天」「上午」），或者过一会儿再问嘛～"
    try:
        since_utc = datetime.utcfromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S")
        msgs = await asyncio.to_thread(get_messages_since, cid, since_utc,
                                       max_count=600)
    except Exception as e:
        log("回顾拉取失败: " + str(e)[:60])
        return "哎呀，小小糯翻记录的时候卡了一下，等会儿再问一次好不好～"
    # 先把成员昵称一次性load进来：否则转录里每个陌生 uid 都会单独发一次 HTTP 查名字
    # （实测 6 小时窗口因此多花 2.9 秒）
    try:
        await asyncio.to_thread(_load_member_names, cid)
    except Exception:
        pass
    msgs = [m for m in msgs if (m.get("content") or {}).get("text", "").strip()]
    # 按窗口终点过滤（"昨天"/"上周末"这类有明确结束时间，不能把今天的消息也算进来）
    if until and until < time.time() - 60:
        msgs = [m for m in msgs if _msg_ts(m) <= until]
    total = len(msgs)
    if total == 0:
        return "（翻了翻小本子）" + (wlabel or "你不在的这段时间") + "群里一条消息都没有，安静得像没人～"
    kept = msgs[-RECAP_MAX_MSGS:]
    transcript, n_shown = _recap_transcript(kept)
    hits, who, sample = _recap_mentions(msgs, uid)
    end_show = until if (until and until < time.time() - 60) else time.time()
    if wlabel:
        win_txt = wlabel + " · " + _fmt_ts(since) + " ~ " + _fmt_ts(end_show)
    else:
        win_txt = _fmt_ts(since) + " ~ " + _fmt_ts(end_show)
    # 🔔 有人找你这行由代码确定，不让 AI 编
    if hits:
        bell = ("🔔 有人找你：" + "、".join(who) + " 提到你 " + str(hits) + " 次"
                + ("（比如「" + sample + "」）" if sample else ""))
    else:
        bell = "🔔 有人找你：没人提到你，安心～"
    mention_txt = ("提到他 " + str(hits) + " 次，提到的人是 " + "、".join(who)) if hits else "无"
    try:
        raw = await asyncio.to_thread(generate_recap, transcript, get_name(uid),
                                      win_txt, mention_txt)
    except Exception as e:
        log("回顾生成失败: " + str(e)[:60])
        raw = ""
    body = {}
    for ln in str(raw or "").split("\n"):
        s = ln.strip()
        for k in ("💬", "🎭", "📌"):
            if s.startswith(k):
                body[k] = s
    lines = ["📮 你不在的时候（" + win_txt + "，群里 " + str(total) + " 条"
             + ("，这里挑最近 " + str(n_shown) + " 条看" if total > n_shown else "") + "）",
             bell]
    for k in ("💬", "🎭", "📌"):
        if body.get(k):
            lines.append(body[k])
    if len(lines) < 3:
        lines.append("（群里挺热闹的，但小小糯没提炼出什么重点，要不你再问细一点？）")
    # 记录冷却
    try:
        st = load_state()
        g2 = st.get(cid) or {}
        cd2 = g2.get("recap_ts") or {}
        cd2[uid] = {"ts": time.time(), "key": wkey}
        g2["recap_ts"] = cd2
        st[cid] = g2
        save_state(st)
    except Exception:
        pass
    log("📮 [" + cid[:8] + "] 回顾: " + get_name(uid)[:12] + " 窗口 " + win_txt
        + " 共 " + str(total) + " 条，提及 " + str(hits) + " 次")
    return "\n".join(lines[:10])


# ---------------------------------------------------------------- 过去 N 天运势趋势（认证群）

TREND_CIDS = {PRIMARY_CID}      # 只在认证妹子群开放（那边才有 fortune_history）
TREND_DEFAULT_DAYS = 7
TREND_MAX_DAYS = 30             # 历史只从 2026-09-04 开始，再长也没数据


def is_trend_cmd(cmd: str):
    """识别趋势指令，返回天数；None = 不是."""
    for p in ["/周运", "/过去一周", "/最近七天", "/周趋势", "/运势趋势", "/走势",
              "/过去7天", "/过去"]:
        if cmd.startswith(p):
            rest = cmd[len(p):].strip()
            if rest.startswith("榜"):   # /周运榜 归榜单分支，不算趋势
                return None
            m = re.search(r"(\d+)", rest)
            n = int(m.group(1)) if m else TREND_DEFAULT_DAYS
            if "半" in rest:
                n = 15
            elif "月" in rest:
                n = 30
            return max(1, min(n, TREND_MAX_DAYS))
    return None


def is_trend_board_cmd(cmd: str) -> bool:
    """识别「群运势榜」指令（必须放在 is_rank_cmd 之前判断，否则会被"排行"吃掉）."""
    return any(k in cmd for k in ("周运榜", "运势排行", "运势榜", "谁运势最好", "谁运气最好"))


def _trend_days(cid: str, uid: str, n: int):
    """取某人最近 n 天的真实运势记录 → [(date_str, score|None)]（从旧到新，缺的为 None）."""
    hist = ((load_state().get(cid) or {}).get("fortune_history") or {}).get(uid) or {}
    today = datetime.now()
    out = []
    for i in range(n - 1, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        sc = hist.get(d)
        out.append((d, int(sc) if isinstance(sc, (int, float)) else None))
    return out


def _trend_spark(scores: list) -> str:
    """迷你走势条：分数 → 方块字符（缺记录用 ·）."""
    blocks = "▁▂▃▄▅▆▇█"
    cells = []
    for sc in scores:
        cells.append("·" if sc is None else blocks[max(0, min(7, sc // 13))])
    return "".join(cells)


def _trend_chong(uid: str, dates: list) -> list:
    """这几天里哪些是「日冲本命」（日期可确定性推算，所以补不出来也能标）."""
    out = []
    try:
        p = get_profile(uid) or {}
        full = p.get("name") or get_full_name(uid)
        bio = str(p.get("bio") or "")
        for d in dates:
            try:
                info = _fortune_calc(uid, full, d, bio)
                if info.get("chong_ben"):
                    out.append(d)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _trend_stats(pairs: list):
    """(日期, 分数) → 统计量（只按有记录的天算）."""
    have = [(d, s) for d, s in pairs if s is not None]
    st = {"n": len(have), "total": len(pairs)}
    if not have:
        return st
    st["avg"] = round(sum(s for _, s in have) / float(len(have)), 1)
    hi = max(have, key=lambda kv: kv[1])
    lo = min(have, key=lambda kv: kv[1])
    st["hi"], st["hi_d"] = hi[1], hi[0]
    st["lo"], st["lo_d"] = lo[1], lo[0]
    # 最长连跌（只看有记录的天，按时间顺序）
    run = best = 0
    best_span = None
    cur_start = None
    for i in range(1, len(have)):
        if have[i][1] < have[i - 1][1]:
            if run == 0:
                cur_start = have[i - 1][0]
            run += 1
            if run > best:
                best, best_span = run, (cur_start, have[i][0])
        else:
            run = 0
    st["drop"] = best
    st["drop_span"] = best_span
    return st


def _wd_cn(date_str: str) -> str:
    """日期 → 周几（中文）."""
    try:
        w = datetime.strptime(date_str, "%Y-%m-%d").weekday()
        return "一二三四五六日"[w]
    except Exception:
        return "?"


def _md_cn(date_str: str) -> str:
    return date_str[5:].replace("-", "/")


async def week_trend(cid: str, uid: str, days: int = TREND_DEFAULT_DAYS) -> str:
    """过去 N 天的真实运势趋势卡（只显示真实记录，没算的留空）."""
    if cid not in TREND_CIDS:
        return ""
    pairs = _trend_days(cid, uid, days)
    st = _trend_stats(pairs)
    name = get_name(uid)
    if st["n"] == 0:
        return ("（翻了翻小本子）过去 " + str(days) + " 天你一次都没找小小糯算过运势呢～"
                "发 /算命 算一次，小小糯就开始给你记了哦～")
    scores = [s for _, s in pairs]
    # 上周同期（用于对比）
    prev = _trend_days(cid, uid, days * 2)[:days]
    pst = _trend_stats(prev)
    lines = ["📈 " + name + " · 过去 " + str(days) + " 天运势（"
             + _md_cn(pairs[0][0]) + " ~ " + _md_cn(pairs[-1][0]) + "）",
             "日期  " + " ".join(_wd_cn(d).rjust(2) for d, _ in pairs),
             "分数  " + " ".join(("—" if s is None else str(s)).rjust(2) for _, s in pairs),
             "走势  " + " ".join(("·" if s is None else "▁▂▃▄▅▆▇█"[max(0, min(7, s // 13))])
                                 .rjust(2) for _, s in pairs),
             "有记录 " + str(st["n"]) + "/" + str(st["total"]) + " 天 · "
             + (("唯一记录 " + str(st["hi"]) + "(" + _wd_cn(st["hi_d"]) + ")")
                if st["n"] == 1 else
                ("均值 " + str(st["avg"]) + " · 最高 " + str(st["hi"])
                 + "(" + _wd_cn(st["hi_d"]) + ")"
                 + " · 最低 " + str(st["lo"]) + "(" + _wd_cn(st["lo_d"]) + ")"))]
    if pst.get("n"):
        diff = round(st["avg"] - pst["avg"], 1)
        arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "=")
        cmp_txt = ("环比上期 " + str(pst["avg"]) + "（" + arrow + str(abs(diff)) + "）")
    else:
        cmp_txt = "上期没记录，没法对比"
    if st.get("drop"):
        sp = st["drop_span"]
        cmp_txt += (" · 最长连跌 " + str(st["drop"]) + " 天（"
                    + _wd_cn(sp[0]) + "→" + _wd_cn(sp[1]) + "）" if sp else "")
    lines.append(cmp_txt)
    chongs = _trend_chong(uid, [d for d, s in pairs if s is not None])
    if chongs:
        lines.append("⚠️ 凶日（日冲本命）：" + "、".join(_wd_cn(d) for d in chongs))
    lines.append("————————————")
    data_txt = ("统计窗口：" + pairs[0][0] + " ~ " + pairs[-1][0] + "\n"
                + "每天分数（— = 那天没算）："
                + "、".join(_wd_cn(d) + " " + ("没算" if s is None else str(s))
                            for d, s in pairs) + "\n"
                + "有记录 " + str(st["n"]) + "/" + str(st["total"]) + " 天，均值 " + str(st["avg"])
                + "，最高 " + str(st["hi"]) + "（周" + _wd_cn(st["hi_d"]) + "）"
                + "，最低 " + str(st["lo"]) + "（周" + _wd_cn(st["lo_d"]) + "）\n"
                + "上期均值：" + (str(pst.get("avg")) if pst.get("n") else "无记录") + "\n"
                + "最长连跌：" + (str(st.get("drop")) + " 天" if st.get("drop") else "无") + "\n"
                + "日冲本命的日子：" + ("、".join(_wd_cn(d) for d in chongs) if chongs else "无"))
    try:
        para = await asyncio.to_thread(generate_trend, data_txt, name)
    except Exception as e:
        log("趋势点评生成失败: " + str(e)[:60])
        para = ""
    para = " ".join(str(para or "").split())
    if not para:
        para = "（翻了翻小本子）这 " + str(days) + " 天里你有记录的有 " + str(st["n"]) + " 天，均值 " + str(st["avg"]) + "～"
    lines.append(para)
    log("📈 [" + cid[:8] + "] 趋势: " + name[:12] + " " + str(days) + " 天，有记录 "
        + str(st["n"]) + " 天，均值 " + str(st["avg"]))
    return "\n".join(lines[:10])


async def week_trend_board(cid: str, days: int = TREND_DEFAULT_DAYS) -> str:
    """群运势榜：过去 N 天谁的平均分最高（只统计有记录的天，至少 2 天才上榜）."""
    if cid not in TREND_CIDS:
        return ""
    fh = (load_state().get(cid) or {}).get("fortune_history") or {}
    today = datetime.now()
    win = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    rows = []
    for uid, uh in fh.items():
        sc = [int(uh[d]) for d in win if isinstance(uh.get(d), (int, float))]
        if len(sc) >= 2:
            rows.append((round(sum(sc) / float(len(sc)), 1), get_name(uid), len(sc), max(sc)))
    if not rows:
        return "过去 " + str(days) + " 天还没有人有 2 天以上的记录呢～多算几次就能上榜啦～"
    rows.sort(reverse=True)
    lines = ["🏆 群运势榜 · 过去 " + str(days) + " 天（按平均分）"]
    for i, (avg, nm, n, hi) in enumerate(rows[:8], 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, str(i) + ".")
        lines.append(medal + " " + nm + " · 均值 " + str(avg) + "（算过 " + str(n) + "/"
                     + str(days) + " 天，最高 " + str(hi) + "）")
    lines.append("（至少算过 2 天才上榜）")
    return "\n".join(lines[:10])


def _room_male(cid: str, uid: str) -> bool:
    """这人是不是男生（档案优先；档案没写就实时查一次并写回档案）。阻塞调用，放线程里跑."""
    def g(v):
        v = str(v or "").strip().lower()
        if v in ("male", "man", "男", "m"):
            return "male"
        if v in ("female", "lesbian", "女", "f", "百合"):
            return "female"
        return ""
    p = get_profile(uid) or {}
    r = g(p.get("gender"))
    if r:
        return r == "male"
    try:
        u = get_user(cid, uid) or {}
    except Exception:
        u = {}
    r = g(u.get("gender"))
    if r and p:
        try:  # 顺手写回档案，避免每次都查
            p["gender"] = r
            upsert_profile(uid, p)
        except Exception:
            pass
    return r == "male"


def _room_greet_text(name: str) -> str:
    """房间群的新客欢迎语（固定模板 + 随机招待品，不消耗 AI 调用）."""
    return (ROOM_GREET_TEMPLATE
            .replace("{name}", name)
            .replace("{item}", random.choice(ROOM_GREET_ITEMS))
            .replace("{line}", random.choice(ROOM_GREET_LINES)))


def _strike_get(group_state: dict, uid: str, reset_h: int) -> int:
    """该客人当前累计调戏次数（超过 reset_h 小时未犯 → 视为 0）."""
    d = (group_state.get("rose_strikes") or {}).get(uid) or {}
    n = int(d.get("n") or 0)
    if n and time.time() - float(d.get("at") or 0) > reset_h * 3600:
        return 0
    return n


def _strike_set(group_state: dict, uid: str, n: int) -> None:
    d = group_state.get("rose_strikes") or {}
    if n <= 0:
        d.pop(uid, None)
    else:
        d[uid] = {"n": n, "at": time.time()}
    group_state["rose_strikes"] = d


def _is_join_msg(m: dict) -> bool:
    """判断是否'加入群聊'系统消息."""
    text = (m.get("content") or {}).get("text", "")
    return "加入了群聊" in text


def _detect_joins(pending: list) -> list:
    """检测 pending 里的新成员加入，返回新成员 uid 列表（去重，排除 bot）."""
    joins = []
    for m in pending:
        if is_self(m.get("sent_by")):
            continue
        if _is_join_msg(m):
            uid = m.get("sent_by", "")
            if uid and uid not in joins:
                joins.append(uid)
    return joins


async def _send(sender, cid: str, text: str, dry: bool) -> bool:
    """发送消息（dry 模式只 log）。"""
    if dry:
        log("[dry] " + text[:60])
        return True
    ok = await sender.send(text, cid)
    log("✅" if ok else "❌")
    return ok


async def _kick_and_notify(sender, cid: str, uid: str, full: str,
                           warn_msg: str, done_msg: str, dry: bool) -> None:
    """三段式移出：提醒本人 → 移出 → 通知群里。移出失败发飞书告警不阻断。"""
    # ① 提醒本人（还在群里，能看到）
    await _send(sender, cid, "🤖 " + warn_msg.format(name=full), dry)
    await asyncio.sleep(2)  # 给被移出者看到提醒的缓冲
    # ② 移出
    try:
        await asyncio.to_thread(remove_member, cid, uid)
        log("🚫 已移出 " + full + " (" + uid[:8] + ")")
        try:
            await asyncio.to_thread(send_weixin, "🚫【小小糯bot】已移出「" + full + "」")
        except Exception:
            pass
    except Exception as e:
        log("踢人失败 " + full + ": " + str(e)[:80])
        try:
            await asyncio.to_thread(send_weixin, "🚨【小小糯bot】移出失败（" + full + "）: " + str(e)[:80])
        except Exception:
            pass
        return
    # ③ 通知群里
    await _send(sender, cid, "🤖 " + done_msg.format(name=full), dry)


def _add_pending_screen(cid: str, uid: str) -> None:
    """撞验证码期间的新成员记入待筛查队列（持久化到 state.json）。"""
    try:
        state = load_state()
        gs = state.get(cid) or {}
        pending = gs.get("pending_screen") or []
        if uid not in pending:
            pending.append(uid)
            gs["pending_screen"] = pending
            state[cid] = gs
            save_state(state)
    except Exception:
        pass


async def _notify_join_delayed(full: str, cid: str) -> None:
    """撞验证码期间新成员延迟筛查的飞书告警。"""
    try:
        await asyncio.to_thread(send_weixin, "🚨【小小糯bot】撞验证码期间新成员「" + full
                                + "」加入（" + cid[:8] + "），已延迟筛查，恢复后自动补筛")
    except Exception:
        pass


async def _rescreen_pending(sender, cid: str, dry: bool) -> None:
    """风控解除后补筛查撞验证码期间延迟的新成员。"""
    state = load_state()
    gs = state.get(cid) or {}
    pending = list(gs.get("pending_screen") or [])
    if not pending:
        return
    gs["pending_screen"] = []
    state[cid] = gs
    save_state(state)
    log("🔄 [" + cid[:8] + "] 风控解除，补筛查 " + str(len(pending)) + " 名新成员")
    passed = await _handle_joins(sender, cid, pending, dry)
    for jid in passed:
        jname = get_full_name(jid)
        await _send(sender, cid, "🤖 " + WELCOME_TEXT.replace("{name}", jname), dry)


async def _handle_joins(sender, cid: str, new_uids: list, dry: bool) -> list:
    """新成员筛查：踢游客 / 踢男 / 提醒未填性别 / 提醒未认证女生。返回放行（需发欢迎语）的 uid 列表。
    撞验证码（get_user/get_verified 返回 None）时延迟筛查：群里说明 + 飞书告警 + 记入 pending_screen。"""
    passed = []
    for uid in new_uids:
        full = get_full_name(uid)
        # 1. 游客账号名
        if any(k in full for k in TOURIST_KEYWORDS):
            if _captcha_active:
                # 撞验证码：延迟踢游客
                _add_pending_screen(cid, uid)
                await _send(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
                await _notify_join_delayed(full, cid)
                continue
            await _kick_and_notify(sender, cid, uid, full, JOIN_WARN_TOURIST, JOIN_DONE_TOURIST, dry)
            continue
        # 2/3/4. 按性别
        try:
            u = await asyncio.to_thread(get_user, cid, uid)
        except Exception:
            u = None
        if u is None:
            # get_user 失败（撞验证码/网络）→ 延迟筛查
            _add_pending_screen(cid, uid)
            await _send(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
            await _notify_join_delayed(full, cid)
            continue
        g = u.get("gender")
        if g == "male":
            await _kick_and_notify(sender, cid, uid, full, JOIN_WARN_MALE, JOIN_DONE_MALE, dry)
        elif g in (None, "", "unknown"):
            # 未填性别：先欢迎，再请管理员留意 + @本人提醒（不踢）
            await _send(sender, cid, "🤖 " + WELCOME_TEXT.replace("{name}", full), dry)
            await _send(sender, cid, "🤖 " + JOIN_WARN_NULL.format(name=full), dry)
        else:
            # female：再查妹子认证状态
            try:
                v = await asyncio.to_thread(get_verified, uid)
            except Exception:
                v = None
            if v is None:
                # 查认证失败（撞验证码）→ 延迟筛查
                _add_pending_screen(cid, uid)
                await _send(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
                await _notify_join_delayed(full, cid)
                continue
            if bool((v or {}).get("verified")):
                # 认证妹子，放行交给主循环发欢迎语
                passed.append(uid)
            else:
                # 女生但未认证：先欢迎，再请管理员留意 + @本人提醒（不踢）
                await _send(sender, cid, "🤖 " + WELCOME_TEXT.replace("{name}", full), dry)
                await _send(sender, cid, "🤖 " + JOIN_WARN_NOT_VERIFIED.format(name=full), dry)
    return passed


def _to_local(sent_at: str) -> str:
    """sent_at(UTC) → 'YYYY-MM-DD HH:MM' 北京时间."""
    from datetime import timedelta, timezone
    try:
        dt = datetime.fromisoformat(sent_at.replace("+00", "+00:00"))
        dt = dt.astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return sent_at[:16]


def _build_profile_text(profile: dict) -> str:
    """从档案构建给 AI 的文本（生成滚动总结用）."""
    lines = ["昵称：" + profile.get("name", "")]
    if profile.get("gender"):
        lines.append("性别：" + profile["gender"])
    if profile.get("signature"):
        lines.append("签名：" + profile["signature"])
    if profile.get("bio"):
        lines.append("简介：" + profile["bio"])
    msgs = profile.get("messages", [])
    if msgs:
        lines.append("发言（" + str(len(msgs)) + "条）：")
        for m in msgs[-30:]:
            lines.append("  - " + m.get("text", "")[:80])
    return "\n".join(lines)


def sync_profiles() -> str:
    """增量同步：拉新消息 → 提取注册人发言 → 更新档案 → 推进游标."""
    cid = _ACTIVE_CID
    state = load_state()
    group_state = state.get(cid) or {}
    since = group_state.get("last_sync_at")

    # 1. 拉增量（或首次全量）
    if since:
        msgs = get_messages_since(cid, since)
    else:
        msgs = get_messages_all(cid, max_count=1000)

    if not msgs:
        return "没有新消息，无需同步～"

    profiles = load_profiles()
    if not profiles:
        return "还没有人注册，无需同步～"

    # 2. 提取注册人发言，追加原文（去重：按 at+text 避免重复追加）+ 更新 last_seen
    updated = 0
    active_uids = []  # 本次有新发言的 uid（用于刷新 bio/last_seen）
    for uid, profile in profiles.items():
        their = [m for m in msgs if m.get("sent_by") == uid and not _is_join_msg(m)]
        if not their:
            continue
        active_uids.append(uid)
        msgs_list = profile.get("messages", [])
        seen = {(x.get("at"), x.get("text")) for x in msgs_list}
        for m in their:
            text = (m.get("content") or {}).get("text", "").strip()
            if not text:
                continue
            at = _to_local(m["sent_at"])
            key = (at, text)
            if key in seen:
                continue
            msgs_list.append({"text": text, "at": at})
            seen.add(key)
        msgs_list.sort(key=lambda x: x.get("at", ""))
        profile["messages"] = msgs_list[-200:]
        profile["last_seen"] = _to_local(their[-1]["sent_at"])  # 最新活跃时间
        updated += 1

    # 2.5 刷新 bio/signature/gender（有发言的人，降频防 captcha）
    for uid in active_uids:
        try:
            u = get_user(cid, uid)
            if u:
                profile = profiles.get(uid)
                if not profile:
                    continue
                if u.get("bio"):
                    profile["bio"] = u["bio"]
                if u.get("quirk"):
                    profile["signature"] = u["quirk"]
                if u.get("gender"):
                    profile["gender"] = str(u["gender"])
        except Exception:
            pass
        time.sleep(0.4)  # 降频

    # 3. 更新滚动总结
    for uid, profile in profiles.items():
        if profile.get("messages"):
            try:
                profile["summary"] = generate_profile_summary(_build_profile_text(profile))
            except Exception:
                pass

    # 4. 保存档案（全局整合，跨群发言都归到同一人的档案）
    save_profiles(profiles)

    # 5. 推进游标
    group_state["last_sync_at"] = msgs[-1]["sent_at"]
    state[cid] = group_state
    save_state(state)

    return "同步完成～更新了 " + str(updated) + " 人的发言档案～"


def sync_all() -> str:
    """对所有群执行增量同步（供 cron --sync 调用）."""
    global _ACTIVE_CID
    results = []
    for cid in GROUPS:
        if cid in MUTE_CIDS:  # 静默监控群：不同步档案
            continue
        _ACTIVE_CID = cid
        try:
            results.append("[" + cid[:8] + "] " + sync_profiles())
        except Exception as e:
            results.append("[" + cid[:8] + "] 同步失败: " + str(e)[:60])
    return "\n".join(results)


async def greet_once(period: str) -> None:
    """发送一次问候到所有群（早/中/晚），用独立 profile 避免和主循环抢锁."""
    sender = Sender(profile_dir="pw_profile_greet")
    try:
        await sender.start()
    except Exception as e:
        log("greet sender init failed: " + str(e)[:80])
        return
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        greeting = generate_greeting(period, today)
    except Exception:
        greeting = ""
    greeting = (greeting or "").strip()
    if not greeting:
        greeting = "姐姐们好呀～"
    for cid in PUSH_CIDS:  # 只在配置了定时推送的群发问候（裳姐姐群、团本群不发）
        ok = await sender.send("🤖 " + greeting, cid)
        if ok:
            # 读回验证：看最近 5 条里有没有 bot 自己刚发的这条问候。
            # 不能只看最后一条——12:00 午安和「骚度排行」定时撞车，排行会插在中间，
            # 若只看 msgs[-1] 会误判「漏发」→ 补发 → 群里收到两遍午安（2026-08-24 实测）。
            await asyncio.sleep(2)
            try:
                msgs = await asyncio.to_thread(get_messages, cid, 5)
                sent = any(
                    is_self(m.get("sent_by"))
                    and greeting[:8] in (m.get("content") or {}).get("text", "")
                    for m in msgs
                )
                if not sent:
                    ok = await sender.send("🤖 " + greeting, cid)
            except Exception:
                pass
        log("greet[" + period + "] " + cid[:8] + ": " + ("✅" if ok else "❌"))
    await sender.close()


async def memory_report_once() -> None:
    """定时记忆汇报（独立 sender，准点执行，机制同问候）."""
    sender = Sender(profile_dir="pw_profile_greet")
    try:
        await sender.start()
    except Exception as e:
        log("memory-report sender init failed: " + str(e)[:80])
        return
    try:
        for cid in PUSH_CIDS:
            await _publish_memory_report(sender, cid, dry=False)
    finally:
        await sender.close()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--interval", type=float, default=0.8)
    ap.add_argument("--sync", action="store_true", help="执行一次增量同步后退出")
    ap.add_argument("--greet", type=str, choices=["morning", "noon", "evening"], help="发送一次问候（morning/noon/evening）")
    ap.add_argument("--memory-report", action="store_true", help="发送一次记忆汇报后退出（定时用）")
    args = ap.parse_args()
    if args.sync:
        print(sync_all())
        return 0
    if args.greet:
        asyncio.run(greet_once(args.greet))
        return 0
    if args.memory_report:
        asyncio.run(memory_report_once())
        return 0
    try:
        asyncio.run(main_loop(args.interval, args.dry, args.once))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
