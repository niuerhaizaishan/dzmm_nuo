"""环境配置层：代理 / 浏览器 channel / python 解释器，统一从环境变量读取。

Docker 化核心：把散落在各文件的硬编码（127.0.0.1:7890 / channel="chrome" /
Hermes venv 路径）收口到环境变量，本地裸机默认值保持兼容，容器/云端用 env 注入。

环境变量：
    DZMM_PROXY          代理地址字符串，如 http://127.0.0.1:7890（默认）或
                        http://host.docker.internal:7890（Docker 内访问宿主机 Clash）。
                        设为空字符串表示直连（云端香港/新加坡直连 dzmm.ai）。
    PLAYWRIGHT_CHANNEL  Playwright 浏览器 channel。默认 chrome（本地系统 Chrome）；
                        容器内设为 chromium（playwright install chromium）。
    PYTHON_BIN          重启 bot 用的 python 解释器路径。默认 sys.executable（容器内即容器 python）。
"""
import os
import sys

_DEFAULT_PROXY = "http://127.0.0.1:7890"


def get_proxy_url():
    """代理 URL 字符串（Playwright 用）。空/空白 → None（直连）。"""
    url = os.environ.get("DZMM_PROXY", _DEFAULT_PROXY)
    url = url.strip() if url else ""
    return url or None


def get_proxies():
    """requests 用的 proxies dict。直连时返回 None。"""
    url = get_proxy_url()
    if not url:
        return None
    return {"http": url, "https": url}


def get_channel():
    """Playwright channel。默认 chrome；容器设 chromium。空字符串 → None（用 playwright 自带 chromium）。"""
    return os.environ.get("PLAYWRIGHT_CHANNEL", "chrome") or None


def get_dzmm_host():
    """DZMM 主站域名（多线路切换：环境变量 DZMM_HOST，默认 www.dzmm.ai）。"""
    host = os.environ.get("DZMM_HOST", "www.dzmm.ai").strip()
    return host or "www.dzmm.ai"


def get_dzmm_origin():
    """DZMM 主站 origin，如 https://www.dzmm.ai。所有 API/Socket.IO 调用都从这里取。"""
    return "https://" + get_dzmm_host()


def get_python_bin():
    """重启 bot 用的 python 解释器。默认当前解释器。"""
    return os.environ.get("PYTHON_BIN") or sys.executable


def in_container() -> bool:
    """是否运行在 Docker 容器内（CONTAINER_MODE=1）。容器内 bot 由 entrypoint 循环管理重启。"""
    return os.environ.get("CONTAINER_MODE") == "1"


def set_process_proxy_env():
    """为 aiohttp/socketio 等读环境变量的库设置 HTTP(S)_PROXY/WS(S)_PROXY。
    直连时清空这些变量（避免走空代理）。返回代理 URL（None=直连）。"""
    url = get_proxy_url()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "WS_PROXY", "WSS_PROXY"):
        if url:
            os.environ[key] = url
        else:
            os.environ.pop(key, None)
    for key in ("NO_PROXY", "no_proxy"):
        os.environ.pop(key, None)
    return url
