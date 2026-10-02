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
import shutil
import sys
from pathlib import Path

# S1（2026-10-01）：路径可用 DZMM_TOKEN_FILE 覆盖 —— 双写期指向 secrets/ 目录
# （目录 bind mount 上 os.replace 可用，单文件挂载的 EBUSY 就此消失）。
# 不设该变量时路径与老版本逐字一致（零回归）。
TOKEN_FILE = Path(
    os.environ.get("DZMM_TOKEN_FILE")
    or (Path(__file__).parent / ".dzmm_token.json")
)
_WARNED: set = set()


def load_cookies() -> list:
    """读 cookie 列表 [{name, value, domain, path, expires, secure, httpOnly}]。

    P12-BL-fix：主文件读不出来（就地写被中断 ⇒ 半截 JSON）时**回退到 `.bak`**，
    别让"没有原子性"直接升级成"凭据没了、只能人工重登"。
    """
    for p in (TOKEN_FILE, TOKEN_FILE.with_name(TOKEN_FILE.name + ".bak")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ck = data.get("cookies", []) if isinstance(data, dict) else []
            if ck:
                if p != TOKEN_FILE:
                    _warn_once("主凭据文件不可用，已回退用 .bak", RuntimeError("fallback"))
                return ck
        except FileNotFoundError:
            continue
        except Exception as e:                                 # noqa: BLE001
            _warn_once("读凭据失败 " + p.name, e)
            continue
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


def _warn_once(what: str, exc: Exception) -> None:
    """同一类失败只往 stderr 吼一次（这个模块不许依赖 runtime.obs）。"""
    key = what + "|" + type(exc).__name__
    if key in _WARNED:
        return
    _WARNED.add(key)
    try:
        sys.stderr.write("⚠️ token_store: {}（{}: {}）\n".format(
            what, type(exc).__name__, str(exc)[:120]))
    except Exception:
        pass


def save_cookies(cookies: list) -> bool:
    """写 cookie 列表（chmod 600，凭证敏感）。**优先原子替换，挂载点上退化为就地写**。

    **这是全项目唯一的凭据写入点**（P5 的"唯一登录者"就建立在这上面）：
    renew_token / headless_relogin / auto_recover 最终都调到这里。

    ## 两条路（P12-BL → P12-BL-fix，2026-09-30 血的教训）

    1. **首选**：临时文件 → `fsync` → `os.replace`（同目录 rename 是原子的）。
    2. **退化**：目标是**单文件 bind mount** 时（线上就是：
       `./.dzmm_token.json:/app/.dzmm_token.json`），rename 会 **EBUSY** ——
       2026-09-30 17:40 起日志里连续 `FAIL: 写回 token JSON 失败` → 3 次后 EMERGENCY，
       续期整整写不回去（token 一直停在旧值）。
       所以 `os.replace` 失败时改成**就地截断写**，并在写之前把老文件拷一份到
       `.bak`（挂载点旁边的普通文件，可写），`load_cookies()` 在主文件读不出来时
       会回退用它 —— 这样"没有原子性"这件事不至于变成"凭据彻底没了"。
    """
    payload = json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2)
    tmp = TOKEN_FILE.with_name(TOKEN_FILE.name + ".tmp")
    bak = TOKEN_FILE.with_name(TOKEN_FILE.name + ".bak")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        try:
            os.replace(tmp, TOKEN_FILE)
        except OSError as e:                                   # EBUSY/EXDEV…
            _warn_once("原子替换失败（多半是单文件 bind mount），退化为就地写", e)
            # 先留一份备份（就地写没有原子性，写坏了还能捞回来）
            try:
                if TOKEN_FILE.exists():
                    shutil.copy2(TOKEN_FILE, bak)
                    os.chmod(bak, 0o600)
            except Exception as e2:                            # noqa: BLE001
                _warn_once("就地写之前备份失败", e2)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(TOKEN_FILE, 0o600)
            except Exception:
                pass
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass
        _note_written()
        return True
    except Exception as e:                                     # noqa: BLE001
        _warn_once("写凭据失败", e)
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
