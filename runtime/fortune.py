"""每日运势（P8 第 4 步从 bot.py 搬出）。

五行/干支/生肖/关系那几张表（`_GAN` `_ZHI` `_WX_*` `_SHENG` `_KE` `_CHONG`
`_REL_SCORE` `_ZHI_SCORE` …）**跟着这一片一起搬**：它们只被这里用，
留在 bot.py 里就变成"模块外的隐式依赖"。

`_fortune_calc` 同时被周趋势（`_trend_chong`）用 —— 所以周趋势必须排在它后面，
从 `runtime.fortune` 拿，而不是回指 bot.py。
"""
import random
import time
from datetime import datetime

from brains import generate_fortune
from profiles import get_profile
from runtime import ctx
from runtime.identity import get_full_name
from runtime.text import _clean_name
from personas import me as _pme                      # 自称：按群人设名（小小糯/薇丝）
import state_store

__all__ = [
    "is_fortune_cmd", "fortune", "_fortune_calc", "_fortune_score",
    "_fortune_level", "_record_fortune", "_get_fortune_history",
    "_char_wx", "_name_wx", "_ganzhi", "_relation",
]


# ⚠️ 只在 is_apology_day（一次性日期，见下）出现，正文里用 {me} 占位符 ——
# 这段是"赔罪话术"，房间里自称是「薇丝」，写死名字会在玫瑰房漏出「小小糯」。
FORTUNE_APOLOGY = """🙇♀️🙇♀️🙇♀️ 姐姐们，{me}来磕头认错啦～前几天把运势算得凶凶的，是{me}不好，这就跪下咚咚咚给姐姐们磕响头赔罪～💦
为了赔罪，今天每一位姐姐的运势，{me}都保证是 90 分以上的大吉～🍀 祝姐姐们好运连连，天天被幸福包围～
姐姐们就原谅软软糯糯的{me}这一次嘛～好不好嘛～♡"""


_GAN = "甲乙丙丁戊己庚辛壬癸"


_ZHI = "子丑寅卯辰巳午未申酉戌亥"


_WX_GAN = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
           "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}


_WX_ZHI = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
           "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水"}


_GZ_BASE = datetime(1900, 1, 1)          # 甲戌日基准


_WX_ORDER = ["木", "火", "土", "金", "水"]


_WX_BASE = {"木": 54, "火": 53, "土": 55, "金": 52, "水": 50}


_WX_LUCK = {"木": ("竹青", "3", "正东"), "火": ("绛红", "7", "正南"),
            "土": ("杏黄", "5", "正中"), "金": ("皓白", "9", "正西"),
            "水": ("月白", "1", "正北")}


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


_REL_SCORE = {"被生": 15, "克": 12, "同": 8, "生": 5, "被克": -26}


_REL_TEXT = {"被生": "受生", "克": "发力", "同": "比和", "生": "泄气", "被克": "受压"}


_LIUCHONG = {"子": "午", "午": "子", "丑": "未", "未": "丑", "寅": "申", "申": "寅",
             "卯": "酉", "酉": "卯", "辰": "戌", "戌": "辰", "巳": "亥", "亥": "巳"}


_LIUHE = {"子": "丑", "丑": "子", "寅": "亥", "亥": "寅", "卯": "戌", "戌": "卯",
          "辰": "酉", "酉": "辰", "巳": "申", "申": "巳", "午": "未", "未": "午"}


_ZHI_SCORE = 8      # 日合本命


_ZHI_PENALTY = -15  # 日冲本命


def is_fortune_cmd(cmd: str) -> bool:
    """识别算命指令."""
    return any(cmd.startswith(p) for p in ["/算命", "/运势", "/占卜", "/今日运势", "/今日运气", "/运气"])


def fortune(cmd_sender: str, cid: str = None) -> str:
    """算命：五行命理算分 → 组装运势消息（硬控 ≤10 行）。`cid` 用来取本群自称."""
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
    _record_fortune(ctx.get_active_cid(), uid, today, score)
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
                + "，记得照着宜忌来，" + _pme(cid) + "都替你看着呢～♡")
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
        lines.append(FORTUNE_APOLOGY.format(me=_pme(cid)))
    return "\n".join(lines)


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
        cnts = (state_store.load_state().get(ctx.get_active_cid()) or {}).get("msg_count") or {}
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


def _record_fortune(cid: str, uid: str, date_str: str, score: int) -> None:
    """写入当日运势（长期历史），失败静默。"""
    try:
        state = state_store.load_state()
        gs = state.get(cid) or {}
        hist = gs.get("fortune_history") or {}
        uh = hist.get(uid) or {}
        uh[date_str] = score
        hist[uid] = uh
        gs["fortune_history"] = hist
        state[cid] = gs
        state_store.save_state(state)
    except Exception:
        pass


def _get_fortune_history(cid: str) -> dict:
    """读取运势历史（uid -> {date: score}），长期保留。"""
    return (state_store.load_state().get(cid) or {}).get("fortune_history") or {}


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
