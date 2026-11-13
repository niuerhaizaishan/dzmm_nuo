"""顶层茶水间游戏入口指令。"""

from runtime.office_store import TEA_CID
from runtime.text import _normalize_command_prefix


_CARD_IDS = {
    "#uno": "3867964",
    "#摸鱼传说": "3834588",
    "#魔塔": "3844599",
}


def card_id(cid: str, text: str) -> str | None:
    if cid != TEA_CID:
        return None
    return _CARD_IDS.get(_normalize_command_prefix(text).casefold())
