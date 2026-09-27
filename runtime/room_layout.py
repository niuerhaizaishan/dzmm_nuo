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
           "wants_photo", "maybe_send", "send_layout", "DEFAULT_LAYOUT"]

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


def is_layout_cmd(text: str, cid: str = None, uid: str = None) -> bool:
    """是不是**屋主**在下"改布局"的命令（含能认出的布局名）."""
    t = str(text or "")
    if not t or not uid:
        return False
    try:
        from runtime.sign import _ROOM_LAYOUT_WORDS, _room_owner
    except Exception:                                        # noqa: BLE001
        return False
    if not _room_owner(cid, uid):
        return False                    # 只有屋主下命令才换布局（其他人由 AI 婉拒）
    if not layout_of(t):
        return False                    # 认不出的风格不发图
    if any(w in t for w in _ROOM_LAYOUT_WORDS):
        return True
    return any(w in t for w in _CHANGE_WORDS)


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
