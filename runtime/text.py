"""中文文本工具（P8 第 4 步从 bot.py 拆出的叶子工具）。

这一组是**纯函数**：不碰网络、不碰状态、不认识"群"。
之所以要单独成模块，是因为后面每一片（`members` / `fortune` / `vote` / `poem` …）
都要用昵称归一化和选项归一化；不抽出来，每片都得回指 `bot.py`。

`_normalize_cn` 是这里最要紧的一个：昵称比较必须走它 ——
繁简、异体字、全半角、空格、大小写任一不同都会被当成两个人，
而"认错人"在这套系统里等于把 A 的档案按到 B 头上。
"""
import re
import unicodedata


__all__ = [
    "_get_opencc", "_normalize_cn", "_simplify", "_strip_parenthesis",
    "_is_stop_word", "_dedupe_options", "_normalize_choice",
    "_extract_deadline_seconds", "_squash", "_clean_name", "_norm_poem",
]


_CJK_VARIANT = {
    '寜': '宁', '寕': '宁', '甯': '宁',
    '爲': '为', '為': '为',
    '夠': '够', '峯': '峰', '羣': '群',
    '裏': '里', '裡': '里', '佈': '布', '併': '并',
    '兇': '凶', '佔': '占', '週': '周', '嚮': '向',
    '麪': '面', '迴': '回', '姦': '奸', '啓': '启',
    '敍': '叙', '妳': '你', '媮': '偷',
}


STOP_WORDS = ("停止", "停下", "结束", "终止")


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


def _is_stop_word(cmd: str) -> bool:
    """识别停止类结束词（停止/停下/结束/终止，带/或不带/）."""
    c = cmd.strip().lstrip("/").strip()
    if not c:
        return False
    for kw in STOP_WORDS:
        if c == kw or c.startswith(kw):
            return True
    return False


def _dedupe_options(options: list) -> list:
    seen = []
    for o in options:
        o = str(o).strip()
        if o and o not in seen:
            seen.append(o)
    return seen


def _normalize_choice(s: str):
    """归一化选项值：A/B/C... → 1/2/3...，返回数字字符串或 None."""
    s = s.strip().upper()
    if s in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
        return s
    mapping = {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5", "F": "6", "G": "7", "H": "8"}
    return mapping.get(s)


def _extract_deadline_seconds(text: str):
    """提取截止时长，返回 (剩余文本, 秒数或 None)."""
    m = re.search(r'(\d+)\s*(分钟|小时|秒)', text)
    if not m:
        return text, None
    n = int(m.group(1))
    secs = {"分钟": 60, "小时": 3600, "秒": 1}[m.group(2)] * n
    remaining = (text[:m.start()] + text[m.end():]).strip()
    return remaining, secs


def _squash(s: str, n: int) -> str:
    """压成单行并截断（简介/签名群内展示用）."""
    s = " ".join(str(s or "").split())
    return s[:n] + ("…" if len(s) > n else "")


def _clean_name(name: str) -> str:
    """昵称只留主名：先截断括号/波浪号等后缀，再留中文；无中文则返回截断后的原名。"""
    for sep in ("（", "(", "～", "~", "【", "[", "·", "-"):
        idx = name.find(sep)
        if idx > 0:
            name = name[:idx]
    cn = "".join(ch for ch in name if "\u4e00" <= ch <= "\u9fff")
    return cn or name.strip()


def _norm_poem(text: str) -> str:
    """规范化诗：去空行 + 限 20 行。

    ⚠️ 原注释写的是"限 9 行（标题占 1 行，合计 ≤10 行，避免 send 的 10 行截断）"——
    那条限制来自**普通账号通道**（1000 字 / 10 换行）。现在超限消息会**自动改走官方
    Bot API**（`runtime/outbound.requires_bot_group_sender`），2026-09-27 实测 66 行的
    消息照发，所以这里放宽到 20 行：够长的宋词/律诗不会再被悄悄砍掉尾巴。
    """
    lines = [ln for ln in (text or "").split("\n") if ln.strip()]
    return "\n".join(lines[:20])
