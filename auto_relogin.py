#!/usr/bin/env python3
"""自动重新登录 DZMM（AppleScript 驱动 Chrome）。

用途：cookie 失效（unauthorized）后，自动打开 dzmm.ai、填账号密码、点登录，
恢复登录态。登录态落在 Chrome（bot 读 Chrome cookie 自动恢复）。

依赖：
- Chrome 运行中（AppleScript 会自动拉起）
- Chrome 已开启「允许 Apple 事件中的 JavaScript」（一次性设置）

用法：
    python auto_relogin.py            # 检测未登录则自动登录
    python auto_relogin.py --force    # 强制走登录流程（不管当前状态）
"""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import env_config

ROOT = Path(__file__).parent
# S1：与 headless_relogin 同一套 DZMM_LOGIN_FILE 口径（本脚本是 macOS 遗留路径，一并改齐）
CONFIG = Path(os.environ.get("DZMM_LOGIN_FILE") or (ROOT / ".dzmm_login.json"))
DZMM_URL = env_config.get_dzmm_origin() + "/"


def load_config():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    return cfg["email"], cfg["password"]


def run_js(js: str) -> str:
    """AppleScript 驱动 Chrome 执行 JS（压缩成单行，转义引号），返回 stdout。"""
    js = " ".join(js.split())
    escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = 'tell application "Google Chrome" to execute front window\'s active tab javascript "%s"' % escaped
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=25)
    return (r.stdout or "").strip()


def run_applescript(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=25)
    return (r.stdout or "").strip()


def check_logged_in() -> bool:
    """读 token JSON 的 access_token，剩余 > 30 分钟视为已登录。"""
    sys.path.insert(0, str(ROOT))
    import token_store
    try:
        v = token_store.get_cookie_value("sb-rls-auth-token")
        if not v:
            return False
        payload = v[len("base64-"):] if v.startswith("base64-") else v
        pad = payload + "=" * (-len(payload) % 4)
        d = json.loads(base64.b64decode(pad))
        return d.get("expires_at", 0) - time.time() > 1800  # 剩余 > 30 分钟
    except Exception:
        return False


def activate_dzmm() -> bool:
    """让 Chrome 激活/打开 dzmm.ai 标签页。"""
    # 先尝试激活已有标签页
    script = (
        'tell application "Google Chrome"\n'
        '  activate\n'
        '  set found to false\n'
        '  repeat with w in windows\n'
        '    set i to 1\n'
        '    repeat with t in tabs of w\n'
        '      if URL of t contains "dzmm.ai" then\n'
        '        set active tab index of w to i\n'
        '        set index of w to 1\n'
        '        set found to true\n'
        '        exit repeat\n'
        '      end if\n'
        '      set i to i + 1\n'
        '    end repeat\n'
        '    if found then exit repeat\n'
        '  end repeat\n'
        'end tell'
    )
    run_applescript(script)
    time.sleep(2)
    # 确认当前标签页是 dzmm
    url = run_js("location.href")
    if "dzmm.ai" in url:
        return True
    # 兜底：直接打开
    run_applescript('tell application "Google Chrome" to open location "%s"' % DZMM_URL)
    time.sleep(5)
    url = run_js("location.href")
    return "dzmm.ai" in url


def click_login_entry() -> bool:
    """点击右上角登录按钮，弹出登录框。"""
    js = (
        '(function(){ var t=null; var els=document.querySelectorAll("button,a,div,span");'
        ' for(var i=0;i<els.length;i++){ var e=els[i]; var x=(e.innerText||e.textContent||"").trim();'
        ' if(x==="登录" && e.tagName==="BUTTON"){ t=e; break; } }'
        ' if(!t) return "no btn"; var r=t.getBoundingClientRect();'
        ' var x=r.left+r.width/2,y=r.top+r.height/2;'
        ' var o={bubbles:true,cancelable:true,view:window,clientX:x,clientY:y,button:0};'
        ' t.dispatchEvent(new MouseEvent("mousedown",o)); t.dispatchEvent(new MouseEvent("mouseup",o));'
        ' t.dispatchEvent(new MouseEvent("click",o)); return "clicked"; })()'
    )
    out = run_js(js)
    time.sleep(2.5)
    return "clicked" in out


def fill_credentials(email: str, password: str) -> bool:
    js = (
        '(function(){ function setV(el,val){'
        ' var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"value").set;'
        ' s.call(el,val); el.dispatchEvent(new Event("input",{bubbles:true}));'
        ' el.dispatchEvent(new Event("change",{bubbles:true})); }'
        ' var em=document.querySelector("input[type=email]");'
        ' var pw=document.querySelector("input[type=password]");'
        ' if(!em||!pw) return "no inputs";'
        ' setV(em,"%s"); setV(pw,"%s");'
        ' return "filled em="+em.value+" pw="+pw.value.length; })()'
    ) % (email, password)
    out = run_js(js)
    return "filled" in out


def click_submit() -> bool:
    js = (
        '(function(){ var t=null; document.querySelectorAll("button").forEach(function(e){'
        ' var x=(e.innerText||e.textContent||"").trim();'
        ' if(x.indexOf("登录")>=0 && e.closest("form")){ t=e; } });'
        ' if(!t) return "no form btn"; var r=t.getBoundingClientRect();'
        ' var x=r.left+r.width/2,y=r.top+r.height/2;'
        ' var o={bubbles:true,cancelable:true,view:window,clientX:x,clientY:y,button:0};'
        ' t.dispatchEvent(new MouseEvent("mousedown",o)); t.dispatchEvent(new MouseEvent("mouseup",o));'
        ' t.dispatchEvent(new MouseEvent("click",o)); return "clicked"; })()'
    )
    out = run_js(js)
    time.sleep(4)
    return "clicked" in out


def login_flow(email: str, password: str) -> bool:
    """完整登录流程，返回是否成功。"""
    if not activate_dzmm():
        print("❌ 无法打开 dzmm.ai")
        return False
    if not click_login_entry():
        # 还在"已登录"态（前端缓存旧 session，refresh_token 已过期），刷新页面触发登出
        print("未找到登录按钮，刷新页面触发登出...")
        run_js("location.reload()")
        time.sleep(6)
        if not click_login_entry():
            print("❌ 刷新后仍找不到登录按钮")
            return False
    if not fill_credentials(email, password):
        print("❌ 填邮箱密码失败（登录框未弹出）")
        return False
    if not click_submit():
        print("❌ 点击提交失败")
        return False
    # 验证登录框是否消失
    probe = run_js(
        '(function(){ return JSON.stringify({em:!!document.querySelector("input[type=email]"),'
        ' pw:!!document.querySelector("input[type=password]")}); })()'
    )
    try:
        d = json.loads(probe)
        if not d.get("em") and not d.get("pw"):
            return check_logged_in()
    except Exception:
        pass
    return check_logged_in()


def main():
    force = "--force" in sys.argv
    email, password = load_config()
    # 1. access_token 有效 → 直接返回
    if not force and check_logged_in():
        print("OK: 已登录（access_token 有效）")
        return 0
    # 2. access_token 过期 → 先打开页面刷新 token（refresh_token 有效时 Supabase 自动刷新）
    print("access_token 过期，尝试打开页面刷新 token...")
    if activate_dzmm():
        time.sleep(6)
        if check_logged_in():
            print("OK: 打开页面刷新 token 成功")
            return 0
    # 3. 刷新失败（refresh_token 也过期）→ 填密码重登
    print("刷新 token 失败，走填密码重新登录...")
    ok = login_flow(email, password)
    if ok:
        print("OK: 自动登录成功")
        return 0
    print("FAIL: 自动登录失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
