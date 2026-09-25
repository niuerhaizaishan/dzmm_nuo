"""双身份 + 昵称解析（P8 第 4 步从 bot.py 拆出的叶子工具）。

P4 起「我自己」有**两个**身份，全项目必须一致地认这两个：
  NUO_UID —— 侧车账号（登录态持有者）：私聊 / 图片 / 分享 / 历史数据里的旧发言
  BOT_UID —— 官方 Bot：群文本发送（P4.1 的 RoutingSender）

回声过滤与「排除自己」必须同时认这两个账号，否则 Bot 的发言会被当成群友 ——
轻则统计污染，重则自触发循环。

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

# 昵称缓存（uid -> 昵称），跨轮次复用
_name_cache: dict = {}

__all__ = [
    "NUO_UID", "BOT_UID", "BOT_NAME", "BOT_NAME_SHORT",
    "self_uids", "is_self", "self_names",
    "_name_cache", "_load_member_names", "get_full_name", "get_name", "is_at_me",
]


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


def _load_member_names(cid: str = None):
    """批量加载成员昵称到缓存（一次 API，替代逐个 get_user）."""
    try:
        members = get_members(cid or ctx.get_active_cid())
        for uid, name in members.items():
            if name:
                _name_cache[uid] = name
    except Exception:
        pass


def get_full_name(uid: str) -> str:
    """uid -> 完整昵称（缓存）."""
    if uid not in _name_cache:
        u = get_user(ctx.get_active_cid(), uid)
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


def is_at_me(text: str) -> bool:
    """判断消息是否 @ 了小小糯（agent）."""
    if "@" not in text:
        return False
    for name in self_names():
        if name and ("@" + name) in text:
            return True
    return False
