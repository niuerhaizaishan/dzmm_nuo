"""双身份 + 昵称解析（P8 第 4 步从 bot.py 拆出的叶子工具）。

P4 起「我自己」有两个身份，全项目必须一致地认这两个：
  NUO_UID —— 侧车账号（登录态持有者）：私聊 / 图片 / 分享 / 历史数据里的旧发言
  BOT_UID —— 官方 Bot：群文本发送（P4.1 的 RoutingSender）

回声过滤与「排除自己」必须同时认这两个账号，否则 Bot 的发言会被当成群友 ——
轻则统计污染，重则自触发循环。

P12-H 起「我自己」是**一串** Bot：`config.json` 的 `bots[]` 里每个启用的 Bot
都是我自己（`register_bots()`），因为每个群的 Bot 是不同的平台身份。
配置里没写 uid 时，可以用**成员表里的显示名**把它认出来
（`resolve_bot_uids()` 启动时查一次；`learn_bot_uid()` 在循环里用缓存兜底）——
否则新 Bot 的第一条发言会被当成群友，可能自问自答。

昵称解析（`get_full_name`/`get_name`）也放这里：判定「@的是不是我」需要它，
而它又是几乎所有模块都要用的东西。原始昵称来自成员表，查不到时兜底读档案。

`_name_cache` 是**可变对象**：bot.py 用 `from ... import _name_cache` 拿到同一个
dict 就地读写，所以留在 bot.py 的两个使用点（`_daily_profile_scan`、
`_find_uid_by_name`）不需要改任何代码。
"""
import os

from dzmm import get_members, get_user
from profiles import get_profile
from runtime import ctx

# ---------------------------------------------------------------- 身份
NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"  # 侧车账号（登录态持有者）
BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()
BOT_NAME = (os.environ.get("DZMM_BOT_NAME") or "小小糯").strip()
BOT_NAME_SHORT = (os.environ.get("DZMM_BOT_SHORT") or "小小糯").strip()

# Bot 名常见的后缀写法：平台上的显示名两种都出现过（`薇丝-bot` / `薇丝`）。
# 匹配与剥 @ 都要同时认，见 `_with_bot_variants`。
_BOT_SUFFIXES = ("-bot", "-机器人", "bot", "机器人", " bot", "机器人～")

# 昵称缓存（uid -> 昵称），跨轮次复用
_name_cache: dict = {}

# P12-AA：在事件循环上遇到"缓存里没有"的 uid 时记在这里（**不发 HTTP**），
# 等主循环的批量预载在工作线程里把真名补上。诊断用 `pending_name_count()`。
_pending_names: set = set()


def pending_name_count() -> int:
    """还等着补真名的 uid 个数（体检/自证用）。"""
    return len(_pending_names)

# P12-H：配置里的所有 Bot（key -> {key,uid,name,short,cid}）。
# 由 `register_bots()` 注入；`_BOT_UIDS`/`_BOT_NAMES` 是它的两个派生集合。
_BOTS: dict = {}
_BOT_UIDS: set = set()
_BOT_NAMES: set = set()

__all__ = [
    "NUO_UID", "BOT_UID", "BOT_NAME", "BOT_NAME_SHORT",
    "self_uids", "is_self", "self_names", "self_names_longest_first",
    "self_names_for", "mentions_self",
    "_name_cache", "_load_member_names", "get_full_name", "get_name", "is_at_me",
    "_pending_names", "pending_name_count", "_on_event_loop",
    "register_bots", "bots", "bot_uids", "bot_names", "learn_bot_uid",
    "resolve_bot_uids",
]


def self_uids() -> set:
    """全部「我自己」的 uid：侧车账号 + 所有配置里的 Bot。"""
    ids = {NUO_UID} | set(_BOT_UIDS)
    if BOT_UID:
        ids.add(BOT_UID)
    return ids


def is_self(uid) -> bool:
    """这条消息是不是我自己发的（侧车账号或任意一个官方 Bot）。"""
    if not uid:
        return False
    return uid in self_uids()


def _with_bot_variants(name: str) -> set:
    """一个 Bot 名，加上它常见的后缀/去后缀写法（`薇丝` / `薇丝-bot` / `薇丝bot` …）。

    P12-S（2026-09-27）：平台上的 Bot 从「薇丝-bot」改名成了「薇丝」。
    历史消息里两种写法都会出现，而名字要同时用于
    「@ 我」判定与「剥 @ 前缀」—— 少了变体，`@薇丝-bot 你好` 会被剥成 `-bot 你好`
    （残余原样进 LLM prompt，见 `self_names_longest_first` 的说明）。
    """
    n = str(name or "").strip()
    if not n:
        return set()
    out = {n}
    low = n.lower()
    for suf in _BOT_SUFFIXES:
        out.add(n + suf)
        # 配置里本来就带后缀的（`薇丝-bot`），也给出剥掉后缀的写法
        if len(n) > len(suf) and low.endswith(suf.lower()):
            out.add(n[: -len(suf)])
    return {x for x in out if x}


def self_names() -> set:
    """判定「@的是我」用的名字集合。

    账号名查成员表；Bot 名**读配置**（Bot API 没有成员接口，查不到）。
    每个名字都带上常见后缀变体（见 `_with_bot_variants`），改名前后都认。
    """
    names = {get_full_name(NUO_UID), get_name(NUO_UID)}
    for nm in set(_BOT_NAMES):
        names |= _with_bot_variants(nm)
    if BOT_UID:
        names |= _with_bot_variants(BOT_NAME) | _with_bot_variants(BOT_NAME_SHORT)
    return {n for n in names if n}


def self_names_for(cid: str = None) -> set:
    """**某个群里**「我」可能被叫到的全部名字 = 账号名 + 各 Bot 名 + 该群人设名。

    为什么要按群算：群里的客人叫的是**人设名**。玫瑰房的管家叫「薇丝」，
    而账号名是「小小糯～」、Bot 名是「薇丝」（2026-09-27 之前叫「薇丝-bot」，
    两种写法都认，见 `_with_bot_variants`）—— 只认 self_names() 的话，
    客人喊一声「薇丝」会被当成在叫别人（房间不接话、调戏也不计数）。

    `personas` 延迟导入：本模块是叶子工具，不能被根模块的导入顺序绑住。
    """
    names = set(self_names())
    try:
        import personas
        names |= personas.all_names(cid)
    except Exception:
        pass
    return {n for n in names if n}


def mentions_self(text: str, cid: str = None) -> bool:
    """这条消息有没有**提到我**（@ 和不带 @ 的直呼都算）。

    给"被点名才回应"的闸门用（房间管家、调戏判定兜底）。
    """
    if not text:
        return False
    return any(nm in text for nm in self_names_for(cid))


# ---------------------------------------------------------------- 多 Bot（P12-H）

def register_bots(bots_list) -> dict:
    """把配置里的**所有** Bot 注册成「我自己」。返回 `{key: uid}`（自证用）。

    接受 dict 或 dataclass（`config_store.BotSpec`）；只有带 `key` 的才算。
    """
    global _BOTS, _BOT_UIDS, _BOT_NAMES
    reg, uids, names = {}, set(), set()
    for b in (bots_list or ()):
        if isinstance(b, dict):
            get = b.get
        else:
            get = lambda k, d="", _b=b: getattr(_b, k, d)      # noqa: E731
        key = str(get("key") or "").strip()
        if not key:
            continue
        uid = str(get("uid") or "").strip()
        rec = {"key": key, "uid": uid,
               "name": str(get("name") or "").strip(),
               "short": str(get("short") or "").strip(),
               "cid": str(get("cid") or "").strip()}
        reg[key] = rec
        if uid:
            uids.add(uid)
        for nm in (rec["name"], rec["short"]):
            if nm:
                names.add(nm)
    _BOTS, _BOT_UIDS, _BOT_NAMES = reg, uids, names
    return {k: v["uid"] for k, v in reg.items()}


def bots() -> list:
    return [dict(v) for v in _BOTS.values()]


def bot_uids() -> set:
    return set(_BOT_UIDS)


def bot_names() -> set:
    return set(_BOT_NAMES)


def _name_matches_bot(name: str) -> str:
    """成员显示名像不像某个 Bot？像就返回那个 Bot 的 key，否则返回 ""。

    匹配规则刻意收紧：**必须整体相等**，或只差一个常见的 bot 后缀
    （`薇丝` / `薇丝-bot` / `薇丝bot` / `薇丝～`）。
    不能写成"以 Bot 名开头" —— 侧车账号叫「小小糯～」、Bot 叫「小小糯」，
    那样会把账号自己也匹配进来（虽然它已经是 self，但规则一旦松就会误伤真人）。
    """
    nm = (name or "").strip()
    if not nm:
        return ""
    for key, rec in _BOTS.items():
        for cand in (rec["name"], rec["short"]):
            if not cand:
                continue
            if nm == cand:
                return key
            tail = nm[len(cand):] if nm.startswith(cand) else None
            if tail is not None and tail.strip(" -_～~·").lower() in ("", "bot", "机器人"):
                return key
    return ""


def learn_bot_uid(uid, name=None) -> bool:
    """把一个"名字对得上配置里的 Bot"的 uid 认成自己。

    只在**成员表缓存**里查名字（不发 API、不进循环的慢路径）。
    返回 True 表示这次真的新认出来了一个（调用方可以打一行日志）。
    """
    uid = str(uid or "").strip()
    # 已经是自己（账号 / 已登记的 Bot / env 里那个 Bot）就不用再认了 ——
    # 用 `is_self()` 而不是裸比 NUO_UID：唯一判定点只能有这一处。
    if not uid or is_self(uid):
        return False
    if name is None:
        name = _name_cache.get(uid) or ""
    key = _name_matches_bot(name)
    if not key:
        return False
    _BOT_UIDS.add(uid)
    if not _BOTS[key].get("uid"):
        _BOTS[key]["uid"] = uid
    return True


def resolve_bot_uids(fetch_members=None) -> dict:
    """给**没写 uid** 的 Bot 从群成员表里查一次（启动时调一次，别放进循环）。

    `fetch_members(cid) -> {uid: 显示名}`，默认用 `dzmm.get_members`。
    查不到不报错（Bot 可能还没被拉进群），返回 `{key: uid}`，空字符串表示没查到。

    两个必须收紧的地方（都是实际会咬人的）：
    - **跳过已经是自己的 uid**：侧车账号的显示名是「小小糯～」，而 Bot 简称是
      「小小糯」—— 不排除的话，第一次跑就会把**账号自己**认成那个 Bot
      （2026-09-26 真的发生过，日志里 `Bot nuo_main 的身份 = 已认到 503e3cb9`）。
      实际危害不大（账号本来就是 self），但它会写进内存里的 Bot 记录、并让日志说谎。
    - **先精确匹配，再退到"只差一个 bot 后缀"**：避免先碰到谁就认谁。
    """
    if fetch_members is None:
        fetch_members = get_members
    out = {}
    for key, rec in _BOTS.items():
        if rec["uid"] or not rec["cid"]:
            out[key] = rec["uid"]
            continue
        try:
            members = fetch_members(rec["cid"]) or {}
        except Exception:                                       # noqa: BLE001
            out[key] = ""
            continue
        cands = {c for c in (rec["name"], rec["short"]) if c}
        hit = ""
        # 第一轮：显示名和配置名完全相同
        for uid, name in members.items():
            if is_self(uid):
                continue
            if str(name or "").strip() in cands:
                hit = str(uid)
                break
        # 第二轮：只差一个 bot 后缀（薇丝 / 薇丝-bot / 薇丝bot）
        if not hit:
            for uid, name in members.items():
                if is_self(uid):
                    continue
                if _name_matches_bot(str(name or "")) == key:
                    hit = str(uid)
                    break
        if hit:
            rec["uid"] = hit
            _BOT_UIDS.add(hit)
        out[key] = hit
    return out


def self_names_longest_first(cid: str = None) -> list:
    """**剥 @ 时必须用这个**，不能用 `self_names()`。

    原因：`self_names()` 是 `set`，迭代顺序不确定。而名字之间可能互为前缀 ——
    侧车账号简化名是「小小糯」、Bot 全名可能是「小小糯-bot」。
    短名先被剥掉时，长名的残余会留在文本里：

        '@小小糯-bot 你好'  --先剥「小小糯」-->  '-bot 你好'

    这个残余会被原样送进 LLM prompt（`_strip_at` / `_detect_perf_query`）。
    按长度倒序剥就没有这个问题。

    顺带说明：**今天就已经存在这个 bug** —— 侧车名是「小小糯～」而简化名是
    「小小糯」，短名先剥时会留下一个「～」。只是残余是一个波浪号，不容易被注意到；
    Bot 改名成「小小糯-bot」之后残余会变成 `-bot`，一眼就能看见。

    `cid` 传了就把它的人设名（如玫瑰房的「薇丝」）一起算进来 —— 否则
    「@薇丝 你好」剥不掉，整句连名字一起塞进 prompt。
    """
    return sorted(self_names_for(cid), key=len, reverse=True)


def _load_member_names(cid: str = None):
    """批量加载成员昵称到缓存（一次 API，替代逐个 get_user）.

    ⚠️ 这是**阻塞**调用（平台 HTTP），必须在工作线程里跑
    （主循环每 10 分钟 `await asyncio.to_thread(_load_member_names, cid)`）。
    """
    try:
        members = get_members(cid or ctx.get_active_cid())
        for uid, name in members.items():
            if name:
                _name_cache[uid] = name
                _pending_names.discard(uid)
    except Exception:
        pass


def _on_event_loop() -> bool:
    """当前代码是不是跑在事件循环线程上（P12-AA 的守卫依据）。"""
    try:
        import asyncio
        asyncio.get_running_loop()
        return True
    except Exception:                                          # noqa: BLE001
        return False


def get_full_name(uid: str) -> str:
    """uid -> 完整昵称（缓存）.

    ⚠️ P12-AA（2026-09-27 体检）：缓存未命中时原来是**直接发 HTTP**（`get_user`），
    而它被一堆 async 函数直接调用（`trend` / `recap` / `sign` / `dice` …）——
    于是"每见一个新成员"就把事件循环堵几秒。

    现在：在事件循环上**不发 HTTP**，退化成"档案里的昵称 → uid 前缀"，
    并把 uid 记进 `_pending_names`，等主循环每 10 分钟的批量预载
    （`_load_member_names`，跑在工作线程）把真名补上。
    工作线程里调用时行为完全不变（该发就发、该缓存就缓存）。
    """
    if uid in _name_cache:
        return _name_cache[uid]
    if _on_event_loop():
        _pending_names.add(uid)
        # 刻意**不写缓存**：写进去会把"还没拿到真名"固化，预载也就懒得覆盖了
        return str((get_profile(uid) or {}).get("name") or "") or uid[:8]
    u = get_user(ctx.get_active_cid(), uid)
    nm = (u.get("fullName", "") if u else "") or ""
    if not nm:
        # 跨群/接口失败时兜底用档案里的昵称，别把 uid 前缀当名字叫出去
        nm = str((get_profile(uid) or {}).get("name") or "")
    _name_cache[uid] = nm or uid[:8]
    _pending_names.discard(uid)
    return _name_cache[uid]


def get_name(uid: str) -> str:
    """uid -> 简化昵称（去掉波浪号/括号后缀）."""
    name = get_full_name(uid)
    for sep in ["～", "~", "（", "("]:
        idx = name.find(sep)
        if idx > 0:
            return name[:idx]
    return name


def is_at_me(text: str, cid: str = None) -> bool:
    """判断消息是否 @ 了我（agent）。`cid` 用来认本群人设名（见 self_names_for）."""
    if "@" not in text:
        return False
    for name in self_names_for(cid):
        if name and ("@" + name) in text:
            return True
    return False
