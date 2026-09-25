"""DZMM 登录态 JSON 存储（替代 Chrome cookie），跨平台（本地/云端通用）。

为什么：原 bot 的登录态依赖 macOS Chrome 的 cookie（browser_cookie3.chrome 读、
v10 加密写回 SQLite），云端 Linux 没有这套东西。本模块把 token 存成纯 JSON 文件，
读/写都走这里，去掉了 Keychain / Cryptodome / SQLite 依赖。

用法：
    import token_store
    token_store.get_cookie_dict()          # {name: value}，给 requests
    token_store.get_cookie_string()        # "name=value; ..."，给 Cookie header
    token_store.get_cookies_for_playwright()  # [{...}]，给 Playwright add_cookies
    token_store.get_cookie_value("sb-rls-auth-token")  # 单个值
    token_store.update_cookie(name, value, **kwargs)   # 更新/新增
    token_store.set_cookies([...])          # 整体替换
"""
import json
import os
from pathlib import Path

TOKEN_FILE = Path(__file__).parent / ".dzmm_token.json"


def load_cookies() -> list:
    """读 cookie 列表 [{name, value, domain, path, expires, secure, httpOnly}]。"""
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        return data.get("cookies", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _note_written() -> None:
    """通知会话层：凭据已更新（P5）。任何异常都忽略 —— 这不是关键路径。"""
    try:
        import sys
        root = str(Path(__file__).resolve().parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        import session_guard
        session_guard.note_credentials_written("token_store.save_cookies")
    except Exception:
        pass


def save_cookies(cookies: list) -> bool:
    """写 cookie 列表（chmod 600，凭证敏感）。

    **这是全项目唯一的凭据写入点**（P5 的"唯一登录者"就建立在这上面）：
    renew_token / headless_relogin / auto_recover 最终都调到这里，
    所以版本递增只在这里做一次即可。
    """
    try:
        TOKEN_FILE.write_text(
            json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except Exception:
            pass
        _note_written()
        return True
    except Exception:
        return False


def get_cookie_dict() -> dict:
    """{name: value}，给 requests 用。"""
    return {c.get("name"): c.get("value") for c in load_cookies()
            if c.get("name") and c.get("value")}


def get_cookie_string() -> str:
    """"name=value; name2=value2"，给 Cookie header 用。"""
    return "; ".join(f"{c.get('name')}={c.get('value')}" for c in load_cookies()
                     if c.get("name") and c.get("value"))


def get_cookies_for_playwright() -> list:
    """Playwright add_cookies 格式的 cookie 列表。"""
    out = []
    for c in load_cookies():
        out.append({
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "expires": c.get("expires"),
            "secure": bool(c.get("secure", False)),
            "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": c.get("sameSite") or ("Lax" if c.get("secure") else "None"),
        })
    return out


def get_cookie(name: str) -> dict | None:
    """读单个 cookie 的完整对象（含 domain/expires/secure/httpOnly）。"""
    for c in load_cookies():
        if c.get("name") == name:
            return c
    return None


def get_cookie_value(name: str) -> str:
    """读单个 cookie 的值。"""
    c = get_cookie(name)
    return c.get("value", "") if c else ""


def update_cookie(name: str, value: str, **kwargs) -> bool:
    """更新/新增单个 cookie（保留其他 cookie 不变）。"""
    cookies = load_cookies()
    for c in cookies:
        if c.get("name") == name:
            c["value"] = value
            c.update(kwargs)
            break
    else:
        cookies.append({"name": name, "value": value, **kwargs})
    return save_cookies(cookies)


def set_cookies(cookies: list) -> bool:
    """整体替换 cookie 列表。"""
    return save_cookies(cookies)
