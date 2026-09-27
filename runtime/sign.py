"""玫瑰房：星座运势与房间互动（P8 第 4 步从 bot.py 搬出）。

包含房间触发词、摘花判定，以及"我是什么星座"的整条链路
（解析生日 → 记住 → 算运势 → 出卡片）。
"""
import asyncio
import hashlib
import random
import re
import time
from datetime import datetime

from brains import generate_horoscope
from dzmm import get_user
from personas import (NUONUO_UID, ROOMS, ROSE_LUCKY_COLORS, ROSE_LUCKY_DIRS,
                      ROSE_LUCKY_ITEMS, ROSE_MOONS, ROSE_SIGNS, ROSE_SIGN_EMOJI,
                      ROSE_SIGN_JI, ROSE_SIGN_MATCH, ROSE_SIGN_WORDS,
                      ROSE_SIGN_YI, ROOM_FLOWER_NOUNS,
                      ROOM_FLOWER_VERBS, ROOM_FLOWER_WORDS, ROOM_MENU_WORDS,
                      ROOM_ORDER_VERBS, me as _pme)   # `{me}`/自称：本群管家叫什么
from runtime.features import on as _feat_on          # P12-A：功能开关（唯一判定点）
from runtime.identity import _load_member_names, get_name, mentions_self, is_self
from runtime.members import _get_msgs_by_uid
from runtime.obs import log
import state_store

__all__ = [
    "_room_owner", "_is_room_trigger", "_is_flower_grab", "_sign_from_md",
    "_sign_intent", "_sign_find", "_sign_parse_birth", "_sign_stored",
    "_sign_remember", "_sign_calc", "_sign_recent", "_split_sections",
    "_sign_card", "_sign_reply",
]


_ROOM_LAYOUT_WORDS = ["改变房间布局", "换房间布局", "改变布局", "换个布局", "重新布置",
                      "布置房间", "布置一下", "换个风格", "改变风格", "装修", "改造房间",
                      "换布置", "重新装修", "改一下房间", "变一下房间"]


_ROOM_CONJURE_WORDS = ["给我", "拿来", "变个", "变出", "我要", "帮我拿", "帮我变",
                       "端来", "来一个"]


_SIGN_FULL = {"白羊座": "白羊", "金牛座": "金牛", "双子座": "双子", "巨蟹座": "巨蟹",
              "狮子座": "狮子", "处女座": "处女", "天秤座": "天秤", "天蝎座": "天蝎",
              "射手座": "射手", "摩羯座": "摩羯", "水瓶座": "水瓶", "双鱼座": "双鱼",
              "天平座": "天秤"}


_SIGN_SHORT = {"白羊": "白羊", "金牛": "金牛", "双子": "双子", "巨蟹": "巨蟹",
               "狮子": "狮子", "处女": "处女", "天秤": "天秤", "天蝎": "天蝎",
               "射手": "射手", "摩羯": "摩羯", "水瓶": "水瓶", "双鱼": "双鱼"}


_SIGN_ME_RE = re.compile(r"^\s*我\s*(" + "|".join(_SIGN_SHORT.keys()) + ")")


_SIGN_NAMES = [s[0] for s in ROSE_SIGNS]


def _room_owner(cid: str, uid: str) -> bool:
    """是不是这间屋子的主人（屋主 + 操作者）——只有他们能下命令."""
    cfg = ROOMS.get(cid) or {}
    return uid in (cfg.get("owners") or set())


def _is_room_trigger(cid: str, uid: str, text: str) -> bool:
    """房间群：这条消息要不要管家回应（被点名 / 主人指令 / 客人点单）.

    P12-A：房间玩法拆成四个可关的功能（`room_*`），**闸门就在这里** ——
    因为"要不要回应"的判定全集中在这个函数里；关掉某项，那么这类消息
    就不会进房间分支（表现 = 管家不接这一类话）。
    """
    cfg = ROOMS.get(cid)
    if not cfg or not text or is_self(uid):
        return False
    t = text.strip()
    if not t or t.startswith("/"):
        return False  # / 指令走权限分支
    if mentions_self(t, cid):
        return True
    if _is_flower_grab(t) and _feat_on(cid, "room_flower"):
        return True  # 摘花 → 进房间分支（男生放任女鬼，其他人由 AI 婉拒）
    if (any(w in t for w in ROSE_SIGN_WORDS) or _sign_find(t)) \
            and _feat_on(cid, "horoscope"):
        return True  # 星座运势（待客服务，客人也能用）
    if _sign_parse_birth(t) and any(k in t for k in ("我是", "生", "生日", "出生", "座")) \
            and _feat_on(cid, "horoscope"):
        return True  # 报生日
    if _room_owner(cid, uid):
        # 主人没点名也可能在下命令
        if not _feat_on(cid, "room_owner_cmd"):
            return False
        if any(w in t for w in _ROOM_LAYOUT_WORDS) \
                or any(w in t for w in _ROOM_CONJURE_WORDS):
            return True
        # P12-O：屋主命令女鬼上场（"变成女鬼"/"演一下女鬼场景"）也算下命令 ——
        # 否则光说"变成女鬼"（没带管家名字）根本进不了房间分支，命令就石沉大海。
        try:
            from runtime import ghost_photo as _gp
            return bool(_gp.wants_photo(t, cid, uid))
        except Exception:                                    # noqa: BLE001
            return False
    # 客人：只有明确的点单才回应（被点名 / 进群问好另外处理）
    if not _feat_on(cid, "room_order"):
        return False
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
    gs = (state_store.load_state().get(cid) or {})
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
        st = state_store.load_state()
        gs = st.get(cid) or {}
        d = gs.get("rose_signs") or {}
        d[uid] = {"sign": sign, "m": int(month), "d": int(day), "at": time.time()}
        gs["rose_signs"] = d
        st[cid] = gs
        state_store.save_state(st)
    except Exception as e:
        log("星座记录失败: " + str(e)[:60])


def _sign_calc(uid: str, sign: str, date_str: str) -> dict:
    """确定性算分：同一个人、同一天、同一个星座 → 结果固定（和认证群五行运势一致）.

    P12-K：在原来的四维（爱情/事业/财运/心情）+ 幸运物/方位/色/宜忌之外，
    补了模板口径要用的三样 —— **健康分**、**幸运数字**、**速配星座**。
    四维的口径一个字没动（星象榜名次与分数行不受影响），新增项各取哈希里
    还没用到的片段，同样是"同人同日同座必相同"。
    """
    h = hashlib.md5((uid + "|" + date_str + "|" + sign).encode("utf-8")).hexdigest()
    nums = [int(h[i:i + 4], 16) for i in range(0, 24, 4)]     # 6 个，够分
    dims = {
        "爱情": 52 + nums[0] % 44,
        "事业": 52 + nums[1] % 44,
        "财运": 52 + nums[2] % 44,
        "心情": 52 + nums[3] % 44,
    }
    match_pool = ROSE_SIGN_MATCH.get(sign) or ("天秤",)
    luck = {
        "item": ROSE_LUCKY_ITEMS[nums[0] % len(ROSE_LUCKY_ITEMS)],
        "dir": ROSE_LUCKY_DIRS[nums[1] % len(ROSE_LUCKY_DIRS)],
        "color": ROSE_LUCKY_COLORS[nums[2] % len(ROSE_LUCKY_COLORS)],
        "yi": ROSE_SIGN_YI[nums[3] % len(ROSE_SIGN_YI)],
        "ji": ROSE_SIGN_JI[nums[4] % len(ROSE_SIGN_JI)],
        # 模板口径的「幸运三件套」：颜色沿用上面的 color，这里补数字与速配星座
        "num": nums[5] % 9 + 1,
        "match": match_pool[int(h[28:32], 16) % len(match_pool)],
    }
    # 五段正文里【健康运势】用得到的一个分（单独取一段哈希，不动上面的四维）
    health = 52 + int(h[24:28], 16) % 44
    # 月相按日期取（全房间一致，仅氛围）
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    luck["moon"] = ROSE_MOONS[(dt.timetuple().tm_yday // 4) % len(ROSE_MOONS)]
    return {"dims": dims, "luck": luck, "health": health,
            "total": sum(dims.values()) // len(dims)}


def _sign_recent(uid: str, cid: str) -> str:
    """该客人最近的发言（给 AI 做个性化点评用）."""
    try:
        their = _get_msgs_by_uid(uid) or []
        return " / ".join(str(t).replace("\n", " ")[:40] for t in their[-4:])
    except Exception:
        return ""


# 五段正文：规范名（内部键）→ 卡片上印的标题
_SIGN_SECTION_TITLES = (
    ("综合", "综合运势"),
    ("爱情", "爱情运势"),
    ("事业学业", "事业学业"),
    ("财富", "财富运势"),
    ("健康", "健康运势"),
)

# 模型可能写别的叫法，一律归到规范名
_SIGN_ALIAS = {
    "综合运势": "综合", "综合": "综合",
    "爱情运势": "爱情", "爱情": "爱情",
    "事业学业": "事业学业", "学业": "事业学业", "事业": "事业学业",
    "财富运势": "财富", "财富": "财富", "财运": "财富",
    "健康运势": "健康", "健康": "健康",
}


def _split_sections(text: str) -> dict:
    """把 AI 写的五段拆成 `{规范名: 正文}`；凑不够三段就返回 `{}`。

    这是"让模型别跑偏"的**安全网**，不是靠模型守规矩：标签写成
    `【爱情运势】`/`【爱情】`/`爱情运势：`/带序号、或者干脆漏掉一两段，
    这里都能收；实在认不出来就返回空，调用方退回旧的单段卡片（绝不开天窗）。
    """
    s = str(text or "").replace("**", "").replace("\r", "")
    s = s.replace("[", "【").replace("]", "】")
    s = s.replace("【", "\n【").replace("】", "】\n")
    out, cur, buf = {}, None, []

    def _flush():
        if cur and buf:
            out.setdefault(cur, " ".join(buf).strip())

    for raw in s.split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        head = re.sub(r"^[0-9]{1,2}\s*[.、)）]\s*", "", ln)      # 去掉"1."这类序号
        if not head:
            continue
        m = re.fullmatch(r"【?\s*([\u4e00-\u9fa5]{2,6})\s*】?\s*[:：]?", head)
        if m and _SIGN_ALIAS.get(m.group(1)):
            _flush()
            cur, buf = _SIGN_ALIAS[m.group(1)], []
            continue
        m = (re.match(r"^【\s*([\u4e00-\u9fa5]{2,6})\s*】\s*(.*)$", head)
             or re.match(r"^([\u4e00-\u9fa5]{2,6})\s*[:：]\s*(.*)$", head))
        if m and _SIGN_ALIAS.get(m.group(1)):
            _flush()
            cur = _SIGN_ALIAS[m.group(1)]
            buf = [m.group(2)] if m.group(2).strip() else []
            continue
        if cur:
            buf.append(ln)
    _flush()
    out = {k: v for k, v in out.items() if len(v) >= 4}          # 空壳段（只有一个词）丢掉
    return out if len(out) >= 3 else {}


async def _sign_card(cid: str, uid: str, sign: str, month: int, day: int,
                     who: str = "客人") -> str:
    """组装星座运势卡（P12-K 模板口径）.

    结构：**分数 + 宜忌打头** → **五段正文**（综合/爱情/事业学业/财富/健康）
    → **幸运三件套**（颜色 / 数字 / 速配星座）。分数与宜忌是我们的确定性数据，
    正文由 AI 一次写五段；模型没按格式来时退回"分数 + 一段点评"（尾巴照旧）。
    """
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
    emo = ROSE_SIGN_EMOJI.get(sign, "🌙")
    try:
        para = await asyncio.to_thread(
            generate_horoscope, sign, el, ruler, lk["moon"], d, lk["yi"], lk["ji"],
            today, who, ("最近发言：" + _sign_recent(uid, cid)) if _sign_recent(uid, cid) else "",
            r["health"], r["total"])
    except Exception as e:
        log("星座点评生成失败: " + str(e)[:60])
        para = ""
    secs = _split_sections(para)
    one = " ".join(str(para or "").split())          # 退回单段时用（把换行/空白压平）
    md_txt = str(month).zfill(2) + "-" + str(day).zfill(2)
    # 只报了座名、没报生日的人（月日都是 0）：这一行不印「00-00生」——
    # 卡片上出现 00-00 很像坏了（P12-K 冒烟时屋主看到的正是这个）。
    birth_txt = (" · " + md_txt + "生") if (month and day) else ""
    lines = [
        "🌹 今日星象 · " + emo + " " + sign + "座",
        "————————————",
        "🪐 " + el + "象 · 守护星" + ruler + " · 月相：" + lk["moon"] + birth_txt,
        "💗爱情" + str(d["爱情"]) + " · 💼事业" + str(d["事业"])
        + " · 💰财运" + str(d["财运"]) + " · 🌙心情" + str(d["心情"]),
        "🍀 幸运：" + lk["item"] + " · " + lk["dir"],
        "📅 宜" + lk["yi"] + " ｜ 忌" + lk["ji"],
        "————————————",
    ]
    if secs:
        for canon, title in _SIGN_SECTION_TITLES:
            if secs.get(canon):
                lines.append("【" + title + "】" + secs[canon])
    else:
        if not one:
            one = ("（抬手拂过窗边那枝玫瑰，花瓣上浮起细碎的光）"
                   + "今天的星象不算平顺，把节奏放慢一点反而顺。"
                   + "（把一杯温水推到你手边）宜" + lk["yi"] + "，忌" + lk["ji"] + "，其余交给时间。")
        lines.append(one)
    lines.extend([
        emo + " 幸运颜色：" + lk["color"],
        emo + " 幸运数字：" + str(lk["num"]),
        emo + " 速配星座：" + lk["match"] + "座",
    ])
    return "\n".join(lines)


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
        return "这个日子" + _pme(cid) + "认不出来……月日再报一次？比如 11 月 5 号。"
    # ③ 问房间整体星象（屋主专属）
    if owner and any(k in t for k in ("房间星象", "今日星象", "今天适合", "屋子运势", "房间运势")):
        card = await _sign_card(cid, cid, "天秤", 9, 23, "Vv姐姐" if uid != NUONUO_UID else "糯糯姐")
        return "（把窗推开一条缝，夜里的香气涌进来）今天这间屋子——\n" + card
    # ④ 星象榜：谁运势最好
    if any(k in t for k in ("谁运势", "谁运气", "运势排行", "星象榜", "谁最好")):
        gs = (state_store.load_state().get(cid) or {})
        rec = gs.get("rose_signs") or {}
        if not rec:
            return "册子上还没有人报过生日呢，报一个，" + _pme(cid) + "才好替你们看。"
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
            return card + "\n（若是你本人的座，说一句「我是" + named + "座」，" + _pme(cid) + "替你记进册子。）"
        got = _sign_stored(cid, uid)
        if not got:
            return ("（合上册子）" + _pme(cid) + "这儿还没记过你的生日呢。"
                    "报一个就好——几月几号，或者直接说你是哪个星座？")
        card = await _sign_card(cid, uid, got[0], got[1], got[2], who)
        return card
    return None
