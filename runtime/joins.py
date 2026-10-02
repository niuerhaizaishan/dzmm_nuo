"""入群链路（P3 Step 3-1，2026-10-01 自 bot.py 原样抽出）。

四件事，按 main_loop 里的先后顺序：
    1. detect_joins        —— 从一批 pending 消息里认出「X 加入了群聊」
    2. screen_members      —— 认证群筛查（踢游客/踢男/提醒未认证；撞验证码 → 延迟筛查）
    3. handle_join_event   —— 分派：筛查 / 筛查关了只欢迎 / 房间迎客 / 普通欢迎 + 逐个发欢迎语
    4. room_greet_text     —— 房间群的新客欢迎语（固定模板 + 随机招待品 + 自我介绍）

**原样搬入**的纪律与 P8 第 4 步一致：逻辑逐字不变，只动「住在哪」。
两处不得不改名的（test_p8s4_split 的自动守卫禁止 bot 与 runtime 定义同名函数）：
    `_send`   → `_send_text`   （bot._send 还被几十个调用点用着，它留下）
    `_feat_on`→ `_gate` + `_feat_on = _gate`
              （调用点保持 `_feat_on(群, "键")` 的字面形态 ——
               test_feature_registry 按这个前缀静态扫描接线，赋值别名即可命中）

两个 bot 侧依赖以参数注入（本模块**不许 import bot**，见 p8s4 守卫）：
    welcome_of     —— 话术表读口（bot._welcome_of）；P3-20 起话术常量真源在
                      本模块（build_welcome），经 bot 壳**传值**注入；
    captcha_active —— 撞验证码旗标的**活读**（bot 进程内全局，别处还会读写它）；
    screen_fn      —— 筛查入口（= bot._handle_joins），既有 monkeypatch 接缝不变。
"""
import asyncio
import random

from dzmm import get_user, get_verified, remove_member
from personas import ROOM_GREET_TEMPLATE, ROOM_GREET_ITEMS, ROOM_GREET_LINES
import personas as _personas
from wx_alert import send_weixin

import state_store
from runtime import features as _features
from runtime import intro_photo as _intro_photo
from runtime.identity import get_full_name, is_self
from runtime.obs import log

# ---------------------------------------------------------------- 欢迎语文案（P3-20 自 bot.py 搬入，真源）

# 认证群欢迎语（含"妹子认证群"字样）—— 别的人群不用它，见 WELCOME_TEXT_PLAIN
WELCOME_TEXT = """🎉 欢迎 {name} 姐姐加入～♡ 我是{me_full}，群里的小秘书～

💬 新人先冒个泡说句话，让姐姐们认识认识你呀～还没注册的话发 /注册 建个档案哦～📝

⚠️ 本群是妹子认证群，不是妹子的小哥哥会被踢出去哦～🚫（没认证的可以私聊糯糯姐发照片认证，哈哈开玩笑的～）

【群简介】姐妹们发骚聊天的地方～可以 🎲发骚、🔗玩调教、😈被羞辱、🎭开公演，还有 🎬剧情游戏、📊投票、🔮算命、📇查档案、🔥发情值排行～发 /说明 看全部功能～♡"""

# 非认证群的欢迎语（中性版）：不写"会被踢""认证"等字样，只介绍功能
WELCOME_TEXT_PLAIN = """🎉 欢迎 {name} 加入～♡ 我是{me_full}，群里的小秘书～

💬 冒个泡说句话，让大家认识认识你呀～还没注册的话发 /注册 建个档案哦～📝

【群简介】{me}会 🔮算命、📇查档案、📝写诗、🎬剧情游戏、📊投票、🎲骰子、🔥发言排行，也能陪你聊天～发 /说明 看全部功能～♡"""


def build_welcome(cid: str, plain: bool = False, *,
                  welcome_text: str, welcome_text_plain: str, phrase_p) -> str:
    """P12-BD：欢迎语可按群覆盖（话术 `welcome_text` / `welcome_text_plain`）。

    认证群那份（WELCOME_TEXT）与中性那份（WELCOME_TEXT_PLAIN）都是**写死的中文**，
    换人设（比如上班群的若瑾）时口吻会串。先问话术表（phrase_p = personas.p），
    没写这个 key 的群原样用常量 —— 逐字节不变，认证群/玫瑰房不受影响。

    常量经 bot 壳**传值**注入 —— test_recovery_ownership patch `bot.WELCOME_TEXT`
    时壳读 bot 全局再传进来，接缝保持（joins 本体不 import bot，p8s4 守卫）。
    """
    key = "welcome_text_plain" if plain else "welcome_text"
    return phrase_p(key, cid) or (welcome_text_plain if plain else welcome_text)

# ---------------------------------------------------------------- 文案（自 bot.py 搬入）

# 撞验证码相关文案
# ⚠️ 自称一律写 `{me}` —— 这里是**代码默认值**（默认人设 = 小小糯），发出去之前
# 会过 `personas.fill(text, cid)`：认证群填「小小糯」、玫瑰房填「薇丝」。
# 直接写死名字的话，玫瑰房会冒出一个客人不认识的「小小糯」。
CAPTCHA_JOIN_NOTICE = ("⚠️ 检测到新成员「{name}」加入，但当前撞平台验证码、暂时核实不了身份～"
                       "恢复后男生和游客会被自动踢出，其他情况会提醒管理员处理哦～♡")

# 新成员筛查（妹子认证群）：踢游客 / 踢男性 / 提醒未填性别
TOURIST_KEYWORDS = ["游客"]
JOIN_WARN_TOURIST = "⚠️ @{name} 你是游客账号，本群是妹子认证群，稍后会将你移出哦～"
JOIN_DONE_TOURIST = "🚫 已移出游客账号「{name}」～（请用正式账号加入）"
JOIN_WARN_MALE = "⚠️ @{name} 检测到你的性别是男，本群是妹子认证群，稍后会将你移出哦～"
JOIN_DONE_MALE = "🚫 已移出「{name}」（原因：男性），本群是妹子认证群～"
JOIN_WARN_NULL = "⚠️ 新成员「{name}」加入但未填性别资料，请管理员留意认证～ @{name} 也请尽快完善资料哦～"
JOIN_WARN_NOT_VERIFIED = "⚠️ 新成员「{name}」加入但未通过妹子认证，请管理员留意～ @{name} 也请尽快完成认证哦～"


# ---------------------------------------------------------------- 基础件

def _gate(cid: str, key: str) -> bool:
    """功能开关的安全封装 —— 与 bot._feat_on 同一份语义（fail-open + 记日志）。

    本模块的调用点统一写 `_feat_on(群, "键")`（下面有赋值别名），
    让 test_feature_registry 的静态扫描把这里的接线也数进去。
    """
    try:
        return _features.on(cid or "", key)
    except Exception as e:                                     # noqa: BLE001
        log("features: 判定失败 key=" + str(key) + " " + str(e)[:60])
        return True


_feat_on = _gate  # 字面别名：调用点写 `_feat_on(...)`（原因见模块 docstring）


async def _send_text(sender, cid: str, text: str, dry: bool) -> bool:
    """发送消息（dry 模式只 log）—— 与 bot._send 同一份语义（那边还有几十个调用点）。"""
    if dry:
        log("[dry] " + text[:60])
        return True
    ok = await sender.send(text, cid)
    log("✅" if ok else "❌")
    return ok


# ---------------------------------------------------------------- 1. 检测

def is_join_msg(m: dict) -> bool:
    """判断是否'加入群聊'系统消息."""
    text = (m.get("content") or {}).get("text", "")
    return "加入了群聊" in text


def detect_joins(pending: list) -> list:
    """检测 pending 里的新成员加入，返回新成员 uid 列表（去重，排除 bot）."""
    joins = []
    for m in pending:
        if is_self(m.get("sent_by")):
            continue
        if is_join_msg(m):
            uid = m.get("sent_by", "")
            if uid and uid not in joins:
                joins.append(uid)
    return joins


# ---------------------------------------------------------------- 2. 筛查

def add_pending_screen(cid: str, uid: str) -> None:
    """撞验证码期间的新成员记入待筛查队列（持久化，P8 单键读写）。"""
    try:
        pending = state_store.group_get(cid, "pending_screen") or []
        if uid not in pending:
            pending.append(uid)
            state_store.group_set(cid, "pending_screen", pending)
    except Exception:
        pass


async def notify_join_delayed(full: str, cid: str) -> None:
    """撞验证码期间新成员延迟筛查的飞书告警。"""
    if not _feat_on(cid, "alert_captcha"):
        return
    try:
        await asyncio.to_thread(send_weixin, "🚨【小小糯bot】撞验证码期间新成员「" + full
                                + "」加入（" + cid[:8] + "），已延迟筛查，恢复后自动补筛")
    except Exception:
        pass


async def kick_and_notify(sender, cid: str, uid: str, full: str,
                          warn_msg: str, done_msg: str, dry: bool) -> None:
    """三段式移出：提醒本人 → 移出 → 通知群里。移出失败发飞书告警不阻断。"""
    # ① 提醒本人（还在群里，能看到）
    await _send_text(sender, cid, "🤖 " + warn_msg.format(name=full), dry)
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
    await _send_text(sender, cid, "🤖 " + done_msg.format(name=full), dry)


async def screen_members(sender, cid: str, new_uids: list, dry: bool, *,
                         welcome_of, captcha_active) -> list:
    """新成员筛查：踢游客 / 踢男 / 提醒未填性别 / 提醒未认证女生。返回放行（需发欢迎语）的 uid 列表。
    撞验证码（get_user/get_verified 返回 None）时延迟筛查：群里说明 + 飞书告警 + 记入 pending_screen。
    """
    passed = []
    # ⚠️ 2026-10-02（生产事故）：`screen` 是**筛查总开关**，但它的把关原先只在
    # **调用点**（`handle_join_event` 里那句 `cid in screen_cids and _feat_on(...)`），
    # 而第二个调用点 `roster.reconcile_members` 直接调本函数时把闸门漏掉了 ——
    # 结果 `screen=False` 的上班群在每次容器重启后被按认证群规矩筛查并踢人。
    #
    # 闸门**下沉到函数自己身上**：第三个调用点就不可能再漏。
    # 返回"全部放行"而不是空列表 —— 语义是"这些人没问题，交给调用方去欢迎"，
    # 与 `handle_join_event` 里"筛查关了但仍旧欢迎"那一支保持一致。
    if not _feat_on(cid, "screen"):
        log("  ⚠️ [" + cid[:8] + "] 本群未开入群筛查（screen=off）-> 不筛查、不动成员")
        return list(new_uids)
    for uid in new_uids:
        full = get_full_name(uid)
        # 1. 游客账号名
        # P12-A：`screen` 是总开关（在调用点），这里三个是**分项**：
        # 关掉 `screen_tourist` 就不再踢游客（也不记待筛），其余筛查照常。
        if any(k in full for k in TOURIST_KEYWORDS) and _feat_on(cid, "screen_tourist"):
            if captcha_active():
                # 撞验证码：延迟踢游客
                if _feat_on(cid, "screen_pending"):
                    add_pending_screen(cid, uid)
                await _send_text(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
                await notify_join_delayed(full, cid)
                continue
            await kick_and_notify(sender, cid, uid, full, JOIN_WARN_TOURIST, JOIN_DONE_TOURIST, dry)
            continue
        if any(k in full for k in TOURIST_KEYWORDS):
            # 游客但"踢游客"已关：放行去发欢迎语，不做任何筛查动作
            passed.append(uid)
            continue
        # 2/3/4. 按性别
        try:
            u = await asyncio.to_thread(get_user, cid, uid)
        except Exception:
            u = None
        if u is None:
            # get_user 失败（撞验证码/网络）→ 延迟筛查
            if _feat_on(cid, "screen_pending"):
                add_pending_screen(cid, uid)
            await _send_text(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
            await notify_join_delayed(full, cid)
            continue
        g = u.get("gender")
        if g == "male" and _feat_on(cid, "screen_gender"):
            await kick_and_notify(sender, cid, uid, full, JOIN_WARN_MALE, JOIN_DONE_MALE, dry)
        elif g in (None, "", "unknown"):
            # 未填性别：先欢迎，再请管理员留意 + @本人提醒（不踢）
            await _send_text(sender, cid, "🤖 " + _personas.fill(welcome_of(cid), cid).replace("{name}", full), dry)
            await _send_text(sender, cid, "🤖 " + JOIN_WARN_NULL.format(name=full), dry)
        else:
            # female（或已关掉"踢男性"时的 male）：再查妹子认证状态
            try:
                v = await asyncio.to_thread(get_verified, uid)
            except Exception:
                v = None
            if v is None:
                # 查认证失败（撞验证码）→ 延迟筛查
                if _feat_on(cid, "screen_pending"):
                    add_pending_screen(cid, uid)
                await _send_text(sender, cid, "🤖 " + CAPTCHA_JOIN_NOTICE.format(name=full), dry)
                await notify_join_delayed(full, cid)
                continue
            if bool((v or {}).get("verified")):
                # 认证妹子，放行交给主循环发欢迎语
                passed.append(uid)
            else:
                # 女生但未认证：先欢迎，再请管理员留意 + @本人提醒（不踢）
                await _send_text(sender, cid, "🤖 " + _personas.fill(welcome_of(cid), cid).replace("{name}", full), dry)
                await _send_text(sender, cid, "🤖 " + JOIN_WARN_NOT_VERIFIED.format(name=full), dry)
    return passed


# ---------------------------------------------------------------- 3. 房间欢迎语（自 bot.py 搬入）

def room_greet_text(name: str, cid: str = None) -> str:
    """房间群的新客欢迎语（固定模板 + 随机招待品，不消耗 AI 调用）.

    模板和候选句里都可能有 `{me}`（本群管家的名字，见 personas.fill）——
    房间管家改过名，欢迎语里必须跟着改，不能写死。

    P12-Q（屋主 2026-09-27）：欢迎语里带上**本群的自我介绍**（"新人进群的欢迎语
    也用新的自我介绍"）。自我介绍没配时（认证群等）`{intro}` 整行收掉，不留空行。
    """
    tpl = (ROOM_GREET_TEMPLATE
           .replace("{name}", name)
           .replace("{item}", random.choice(ROOM_GREET_ITEMS))
           .replace("{line}", random.choice(ROOM_GREET_LINES)))
    intro = (_personas.p("intro_text", cid) or "").strip()
    tpl = tpl.replace("{intro}", intro) if intro else tpl.replace("\n{intro}\n", "")
    return _personas.fill(tpl, cid)


# ---------------------------------------------------------------- 4. 分派（自 bot.py main_loop 搬入）

async def handle_join_event(sender, cid: str, joins: list, dry: bool, group_state: dict, *,
                            screen_cids, room_cids, screen_fn, welcome_of) -> None:
    """入群分派：筛查 / 筛查关了只欢迎 / 房间迎客 / 普通欢迎 + 逐个发欢迎语。

    调用方（bot.main_loop）只保留"剧情进行中不迎新"的故事态闸门。
    `screen_fn` = bot._handle_joins：按名注入让既有的 monkeypatch 接缝原样可用。
    """
    if cid in screen_cids and _feat_on(cid, "screen"):
        log("🎉 [" + cid[:8] + "] 新成员加入 " + str(len(joins)) + " 人，开始筛查")
        passed = await screen_fn(sender, cid, joins, dry)
        wtxt = _personas.fill(welcome_of(cid), cid)
    elif cid in screen_cids:
        # 「入群筛查」关了：**不再踢人**，但仍要欢迎 ——
        # 所以这里退化成"普通群"的处理方式（中性欢迎语）。
        # ⚠️ 闸门只包住筛查这一支，不能包住整个入群块，
        # 否则关掉筛查会把欢迎语、房间迎客一起关掉。
        log("🎉 [" + cid[:8] + "] 新成员加入 " + str(len(joins)) + " 人（筛查已关闭，只欢迎）")
        passed = joins
        wtxt = _personas.fill(welcome_of(cid, plain=True), cid)
    elif cid in room_cids:
        # 房间群：主动问好 + 屋主规矩 + 花瓣变出招待品
        # （女鬼期间屋子没人，不迎客）
        # P12-A：`screen` 是入群筛查总开关（只管认证群那套踢人）；
        # 房间迎客另有 `room_welcome`，两者互不影响。
        if not _feat_on(cid, "room_welcome"):
            passed, wtxt = [], ""
        elif (group_state.get("rose_ghost") or {}).get("active") \
                and _feat_on(cid, "room_ghost"):
            log("👻 [" + cid[:8] + "] 有客人进房，但管家不在（女鬼期间），不迎客")
            passed, wtxt = [], ""
        else:
            log("🌹 [" + cid[:8] + "] 有客人进房 " + str(len(joins)) + " 人，上茶")
            for jid in joins:
                await _send_text(sender, cid,
                                 "🤖 " + room_greet_text(get_full_name(jid), cid), dry)
            # P12-L：问好之后补一张立绘（一次进房只发一张，多人同时进也不刷屏）
            if not dry:
                await _intro_photo.send(sender, cid, reason="进房问好")
            passed, wtxt = [], ""
    else:
        log("🎉 [" + cid[:8] + "] 新成员加入 " + str(len(joins)) + " 人，只发欢迎语")
        passed = joins
        wtxt = _personas.fill(welcome_of(cid, plain=True), cid)
    for jid in passed:
        jname = get_full_name(jid)
        if not _feat_on(cid, "screen_welcome"):
            continue
        await _send_text(sender, cid, "🤖 " + wtxt.replace("{name}", jname), dry)
