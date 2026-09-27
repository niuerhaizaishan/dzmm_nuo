"""P12-N：房间布局配图（改布局 → 发对应的房间图，恢复 → 回到玫瑰房）。

屋主 2026-09-27 指定：四种布局各一张图，**玫瑰房是初始布局**，
"恢复布局后自动是玫瑰房"。图托管在自家域名（同 P12-L 的立绘）：

    https://www.nuonuo.pw/img/room/{rose,pool,bbq,shenwenshi}.jpg

两条触发路径：

1. **改布局**：屋主说「{me}改变房间布局（审问室风格）」这类 → AI 描述新布局之后补图；
2. **屋子复原**：女鬼剧本结束、屋主回来（`ROOM_GHOST_BACK`）→ 补**玫瑰房**那张。

口径（刻意保守）：只认**屋主**的、且**能认出是哪种布局**的话。
认不出的风格（比如"赛博朋克"）**不发图** —— 宁可不发，也不要发一张不相干的房间。
"""
import re

from runtime import bot_photo

__all__ = ["LAYOUTS", "FILE_OF", "URL_TMPL", "layout_of", "photo_url",
           "wants_photo", "maybe_send", "send_layout", "DEFAULT_LAYOUT",
           "is_layout_cmd", "_bare_layout"]

DEFAULT_LAYOUT = "玫瑰房"          # 初始布局：恢复/复原/没认出来时的那张
URL_TMPL = "https://www.nuonuo.pw/img/room/{}.jpg"

# 布局名 → 文件名（ASCII，避免中文 URL 转义）
FILE_OF = {
    "审问室": "shenwenshi",
    "玫瑰泳池": "pool",
    "户外烧烤": "bbq",
    "玫瑰房": "rose",
}

# 关键词表：**顺序即优先级**，先命中的赢
LAYOUTS = (
    ("审问室", ("审问", "审讯", "审训", "拷问")),
    ("玫瑰泳池", ("泳池", "游泳池", "水池", "池子", "池边", "泡水")),
    ("户外烧烤", ("烧烤", "烤肉", "烤架", "烤炉", "bbq", "户外", "露台")),
    (DEFAULT_LAYOUT, ("玫瑰房", "原样", "恢复", "复原", "初始", "原来的样子",
                      "换回来", "回到原来", "改回来", "撤回")),
)

# "改布局"的意图词（`_ROOM_LAYOUT_WORDS` 之外的自然说法也认）。
# 刻意用**两字以上**的词：单字"改/变/换"太松（"改天再布置审问室"也会命中）。
_CHANGE_WORDS = ("改变", "换成", "换回", "换一个", "改成", "改回", "变成", "变换",
                 "布置", "装修", "改造", "弄成", "装成", "布成", "重新",
                 "恢复", "复原", "回到", "来一个", "来个", "风格")

# 去掉称呼（@薇丝 / 薇丝）与标点后，整句**正好**是一个布局名时的清洗规则。
# 屋主 2026-09-27 问"必须得说房间改成泳池才能触发场景吗" → 不必：光说「泳池」也行，
# 但"泳池真好看""我想去泳池"这种**不算**（不是"整句就是布局名"）。
_NOISE_RE = re.compile(r"[@\s,，。.!！?？:：；;、~～\-—]+")
_BARE_SUFFIX = ("风格", "房间", "模式", "吧", "呢", "呀")
_BARE_MAX = 8          # "户外烧烤"=4，"玫瑰泳池"=4；再长就不是报名字了


def layout_of(text: str) -> str:
    """这句话在说哪种布局；认不出来返回空串."""
    t = re.sub(r"\s+", "", str(text or "")).lower()
    if not t:
        return ""
    for name, kws in LAYOUTS:
        if any(k in t for k in kws):
            return name
    return ""


def photo_url(layout: str) -> str:
    f = FILE_OF.get(str(layout or ""))
    return URL_TMPL.format(f) if f else ""


def _bare_layout(text: str, cid: str = None) -> str:
    """整句**就是一个布局名**时返回那个布局名，否则空串.

    先去称呼（`@薇丝` / `薇丝`）与标点，再看剩下的是不是正好等于布局名或它的关键词；
    允许尾巴带"风格/房间/模式/吧/呢/呀"。这样：

        「泳池」「薇丝 泳池」「@薇丝 审问室」「烧烤」「玫瑰房」 → 认 ✓
        「我想去泳池」「泳池真好看」「泳池照片」              → 不认 ✗（不是在报名字）
    """
    t = str(text or "")
    try:
        from runtime.identity import self_names_for
        for nm in sorted([n for n in self_names_for(cid) if n], key=len, reverse=True):
            t = t.replace("@" + nm, " ").replace(nm, " ")
    except Exception:                                        # noqa: BLE001
        pass
    t = _NOISE_RE.sub("", t)
    if not t or len(t) > _BARE_MAX:
        return ""
    for suf in _BARE_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf):
            t = t[: -len(suf)]
    for name, kws in LAYOUTS:
        if t == name or t in kws:
            return name
    return ""


def is_layout_cmd(text: str, cid: str = None, uid: str = None) -> bool:
    """是不是**屋主在点名**让管家换布局（认得出布局名 + 有改的意图，或整句就是布局名）.

    屋主 2026-09-27："我觉得还是前面必须加薇丝才好吧" —— 所以**必须点名**
    （`@薇丝` / 说出"薇丝"）。光说「泳池」，或者说"改成泳池"但没叫她，都**不算**命令：
    闲聊里蹦出一个布局名就把房间换了，代价比少认几次大得多。
    """
    t = str(text or "")
    if not t or not uid:
        return False
    try:
        from runtime.identity import mentions_self
        from runtime.sign import _ROOM_LAYOUT_WORDS, _room_owner
    except Exception:                                        # noqa: BLE001
        return False
    if not _room_owner(cid, uid):
        return False                    # 只有屋主下命令才换布局（其他人由 AI 婉拒）
    if not mentions_self(t, cid):
        return False                    # 必须点名（见 docstring）
    if not layout_of(t):
        return False                    # 认不出的风格不发图
    if any(w in t for w in _ROOM_LAYOUT_WORDS):
        return True
    if any(w in t for w in _CHANGE_WORDS):
        return True
    return bool(_bare_layout(t, cid))   # 光报布局名也算，但同样要先点名


def wants_photo(text: str, cid: str = None, uid: str = None) -> bool:
    """别名：改布局 + 认得出布局名（`maybe_send` 的判定点）."""
    return is_layout_cmd(text, cid, uid)


async def send_layout(sender, cid: str, layout: str, reason: str = "") -> bool:
    """发某张布局图（屋子复原走这条，布局名直接给 `玫瑰房`）."""
    url = photo_url(layout)
    if not url:
        return False
    return await bot_photo.send(sender, cid, url,
                                reason="{}·{}".format(reason or "布局", layout))


async def maybe_send(sender, cid: str, text: str, uid: str = None,
                     reason: str = "") -> bool:
    """自然语言路径：判意图 → 判重复 → 发对应布局图."""
    if not is_layout_cmd(text, cid, uid):
        return False
    layout = layout_of(text)
    if bot_photo.dup(cid, "layout:" + layout):
        return False
    return await send_layout(sender, cid, layout, reason=reason or "改布局")
