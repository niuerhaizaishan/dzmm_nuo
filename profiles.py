"""成员注册档案 — 按人整合的全局档案。

存储后端自 P7 起交给 `state_store`（默认 SQLite）：
**函数名与签名保持不变**，所以 `bot.py` / `batch_register.py` / 测试脚本的调用点都不用改。

为什么不再直接写 JSON（事实已核实）：
  `profiles.json` 是**文件 bind mount**，`os.replace` 会 `Device busy`，
  所以旧实现只能 `write_text` **原地覆盖**（非原子）。
  而"读整个文件 → 改 → 写回整个文件"在有**两个写者**时会丢更新
  —— `bot.py`（运行中）与手工脚本 `batch_register.py` 同时跑就会发生。
  SQLite 把整段写放进一个事务、按 uid 增量 upsert，两者互不覆盖。

`DZMM_STORE=json` 可回退到原来的 JSON 行为（文件保留不删）。
"""
from pathlib import Path

import state_store

ROOT = Path(__file__).resolve().parent
PROFILES_PATH = ROOT / "profiles.json"          # 保留：回滚后端与体积统计仍用它


def load_profiles() -> dict:
    """读取所有档案 {uid: profile}（容错：文件损坏/不存在时返回空 dict）。"""
    return state_store.load_profiles()


def save_profiles(data: dict) -> bool:
    """写入档案（SQLite 后端按 uid 增量 upsert，单事务）。"""
    return state_store.save_profiles(data)


def get_profile(uid: str) -> dict | None:
    """查单个成员的档案。"""
    return state_store.get_profile(uid)


def upsert_profile(uid: str, profile: dict) -> bool:
    """注册/更新单个成员的档案。"""
    return state_store.upsert_profile(uid, profile)
