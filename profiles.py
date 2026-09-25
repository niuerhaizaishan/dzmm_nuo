"""成员注册档案 — JSON 持久化 + 原子写入（全局，按人整合）."""
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILES_PATH = ROOT / "profiles.json"


def load_profiles() -> dict:
    """读取所有档案 {uid: profile}（容错：文件损坏/不存在时返回空 dict）."""
    try:
        if PROFILES_PATH.exists():
            return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_profiles(data: dict) -> bool:
    """写入档案（write_text 直接写，兼容单文件 bind mount——os.replace 在挂载点上会报 Device busy）。"""
    try:
        PROFILES_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def get_profile(uid: str) -> dict | None:
    """查单个成员的档案."""
    data = load_profiles()
    return data.get(uid)


def upsert_profile(uid: str, profile: dict) -> bool:
    """注册/更新单个成员的档案."""
    data = load_profiles()
    data[uid] = profile
    return save_profiles(data)
