"""把构建好的游戏单文件发布到 Gamefy 工坊（登录 → 上传 zip → 核验 → 发布）。

为什么需要它：图集这类游戏的前端是平台侧托管的单文件，机器人部署和服务器重启都不会
更新它；只有「工坊发布」才会换掉线上版本。手工点的工作坊每次都要重复同一串动作，
这里固化下来，并且**先核验再发布**，避免把没生效的文件推上线。

前置条件
  * 运行环境里有 Playwright + Chromium（dzmm-bot 容器已具备）。
  * 使用**拥有该游戏**的账号：平台要求「登录 + VIP1 以上 + 拥有该游戏」。
    三个机器人账号（小小糯／Λʌ／若瑾）都没有游戏作品，必须用卡主账号。

用法
  # 1) 登录并保存会话（密码只从环境变量读，不写盘、不进日志）
  DZMM_OWNER_EMAIL=... DZMM_OWNER_PASSWORD=... \
      python tools/publish_game_card.py login --state /tmp/state.json

  # 2) 上传并核验（不发布）
  python tools/publish_game_card.py upload --state /tmp/state.json \
      --game-id 3799187 --html tmp_gallery_deploy/xxx/index.html \
      --expect 私密相册

  # 3) 发布（确认后才真正换线上版本）
  python tools/publish_game_card.py publish --state /tmp/state.json --game-id 3799187 --confirm

  # 4) 发布后核对线上字节（需要会话，因为 /api/gamefy/<id> 要登录）
  python tools/publish_game_card.py verify --state /tmp/state.json --game-id 3799187 --expect 私密相册

要点（踩过的坑）
  * zip 必须用正斜杠、且带一层根目录：`gallery/publish/index.html`。平台会去掉单层根
    目录再解压；直接用 PowerShell `Compress-Archive` 打出 `publish/index.html` 会因条目
    分隔符/根目录规则被判「解压失败」。
  * 上传对话框要等出现「拖入 .zip 文件」再投递文件；点「发布」后的确认框出现得比刷新慢，
    必须先等「确认发布」再点。
  * 预览是从「已保存的 Git 版本」加载的，所以核验要在导入完成后刷新预览再读文本。
  * 竖屏预览里分区标签显示短名（私密），宽屏才是私密相册；--expect 用 | 分隔多个候选。
  * 光看文本标记不够：私密图漏传身份头时列表照样正常、图片全 401，要用 --preview-private。
"""
import argparse
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ORIGIN = "https://www.dzmm.ai"
WORKBENCH = ORIGIN + "/studio/game-creation/workbench?character_id={game}"
ARGS = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"]


def die(message):
    print("失败:", message)
    sys.exit(1)


def make_zip(html_path):
    """平台要求：正斜杠路径 + 单层根目录（会被去掉）。"""
    data = Path(html_path).read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("gallery/publish/index.html", data)
    print(f"zip 就绪: {len(data)} 字节 html → {len(buffer.getvalue())} 字节 zip")
    return buffer.getvalue()


def session(state):
    from playwright.sync_api import sync_playwright
    return sync_playwright


def cmd_login(args):
    email, password = os.environ.get("DZMM_OWNER_EMAIL"), os.environ.get("DZMM_OWNER_PASSWORD")
    if not email or not password:
        die("需要 DZMM_OWNER_EMAIL / DZMM_OWNER_PASSWORD 环境变量")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            "/tmp/publisher_profile", headless=True,
            viewport={"width": 1440, "height": 900}, user_agent=UA, args=ARGS)
        page = ctx.new_page()
        page.goto(ORIGIN + "/", timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if page.locator("text=登录").count():
            page.locator("text=登录").first.click(timeout=15000)
            page.wait_for_timeout(2500)
            page.fill("input[type=email]", email)
            page.fill("input[type=password]", password)
            page.locator("button", has_text="登录").last.click(timeout=15000)
            page.wait_for_timeout(9000)
        cookie = next((c for c in ctx.cookies() if c["name"] == "sb-rls-auth-token"), None)
        if not cookie:
            ctx.close()
            die("登录失败（可能撞验证码）：需要人工一次，或用已有 profile 重试")
        ctx.storage_state(path=args.state)
        os.chmod(args.state, 0o600)
        page.goto(ORIGIN + "/studio/game-creation", timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        body = page.inner_text("body")
        print("登录账号区域:", " / ".join(x for x in body.splitlines() if x.strip())[:120])
        print("会话已保存:", args.state)
        ctx.close()


def open_workbench(browser, args):
    ctx = browser.new_context(storage_state=args.state, viewport={"width": 1600, "height": 950},
                              user_agent=UA)
    page = ctx.new_page()
    page.goto(WORKBENCH.format(game=args.game_id), timeout=60000, wait_until="domcontentloaded")
    page.wait_for_selector("text=上传压缩包", timeout=60000)
    page.wait_for_timeout(4000)
    return ctx, page


def preview_state(page):
    """读工坊预览帧里的分区标签与正文。"""
    for item in page.frames:
        try:
            if not item.evaluate("() => !!document.querySelector('.category[data-category]')"):
                continue
            labels = item.evaluate(
                "() => [...document.querySelectorAll('.category')].map(n => n.innerText.trim())")
            text = item.evaluate("() => document.body ? document.body.innerText : ''")
            return [x for x in labels if x], text
        except Exception:
            continue
    return [], ""


def check_preview_private(page, wait_ms=9000):
    """在工坊预览里切到私密相册，确认缩略图真的请求回来了（成员视角）。

    只核验文本标记不够：私密图走成员鉴权路由，前端漏传身份头时列表照样能出来、
    图片却 401，所以这里直接数 naturalWidth 大于 0 的图。
    """
    frame = None
    for item in page.frames:
        try:
            if item.evaluate("() => !!document.querySelector('.category[data-category]')"):
                frame = item
                break
        except Exception:
            continue
    if frame is None:
        return {"ok": False, "why": "找不到预览帧"}
    if not frame.evaluate("""() => {
        const tab = [...document.querySelectorAll('.category')].find(n => n.dataset.category === '私密相册');
        if (!tab) return false;
        tab.click();
        return true;
    }"""):
        return {"ok": False, "why": "预览里没有私密相册标签（不是成员视角？）"}
    page.wait_for_timeout(4000)
    # 列表是懒加载的，先滚到底把可见区以外的图也触发一下，再判定。
    frame.evaluate("() => { const s = document.querySelector('#app-shell'); if (s) s.scrollTop = s.scrollHeight; }")
    page.wait_for_timeout(wait_ms // 2)
    result = frame.evaluate("""() => {
        const images = [...document.querySelectorAll('#gallery img')];
        const loaded = images.filter(i => i.complete && i.naturalWidth > 0);
        const errors = [...document.querySelectorAll('.card-image-button')]
            .filter(b => b.dataset.loadState === 'error').length;
        return {total: images.length, loaded: loaded.length, errors,
                first: loaded.length ? [loaded[0].naturalWidth, loaded[0].naturalHeight] : null,
                statuses: [...document.querySelectorAll('.image-status')].slice(0, 3).map(s => s.textContent)};
    }""")
    # 懒加载下「全部加载完」不现实：只要有一张拿到字节、且没有失败态就算通过。
    result["ok"] = result["loaded"] > 0 and result["errors"] == 0
    return result


def cmd_upload(args):
    payload = make_zip(args.html)
    Path("/tmp/upload.zip").write_bytes(payload)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=ARGS)
        ctx, page = open_workbench(browser, args)
        try:
            page.get_by_role("button", name="上传压缩包").first.click()
            page.wait_for_selector("text=拖入 .zip 文件", timeout=20000)
            with page.expect_file_chooser(timeout=15000) as info:
                page.get_by_text("拖入 .zip 文件", exact=False).first.click()
            info.value.set_files("/tmp/upload.zip")
            print("已投递 zip")
            ok = False
            for _ in range(30):
                page.wait_for_timeout(2000)
                body = page.inner_text("body")
                if any(m in body for m in ("导入成功", "上传成功", "解压完成")):
                    ok = True
                    break
                if "解压失败" in body or "失败" in body:
                    die("平台导入失败：" + body[-200:])
            if not ok:
                print("未捕获导入成功提示，继续刷新预览核验")

            page.get_by_role("button", name="刷新").first.click()
            page.wait_for_timeout(4000)
            # 导入是否落库，唯一可靠的判据是「刷新预览后内容变了」：平台的成功提示会一闪而过，
            # 而旧版本同样有私密分区，所以必须核验具体标签文字，不能只找关键词。
            expected = [m for m in (args.expect_tabs or "").split(",") if m]
            markers = [m for m in (args.expect or "").split("|") if m]
            matched, merged = False, ""
            for attempt in range(12):
                if page.locator("[role=dialog]").count():
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                page.get_by_role("button", name="刷新").first.click()
                page.wait_for_timeout(4000)
                labels, merged = preview_state(page)
                if expected and labels == expected:
                    matched = True
                    print(f"[{attempt}] 预览标签已更新: {labels}")
                    break
                if not expected and markers and any(m in merged for m in markers):
                    matched = True
                    break
                print(f"[{attempt}] 预览还是旧内容，标签={labels}")
            if not matched:
                die("刷新 12 次后预览仍未变成新内容（导入可能没落库），未发布：" + str(labels))
            if markers and not any(m in merged for m in markers):
                die(f"预览里没找到「{' 或 '.join(markers)}」，未发布：" + merged[:200].replace("\n", " "))
            print("预览核验通过:", expected or args.expect)
            print("预览片段:", merged.replace("\n", " ")[:220])
            if args.preview_private:
                result = check_preview_private(page)
                print("私密相册预览核验:", json.dumps(result, ensure_ascii=False))
                if not result.get("ok"):
                    die("私密相册图片在预览里没加载出来，未发布")
            ctx.storage_state(path=args.state)
        finally:
            ctx.close()
            browser.close()


def cmd_publish(args):
    if not args.confirm:
        die("发布会覆盖线上版本，需要 --confirm")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=ARGS)
        ctx, page = open_workbench(browser, args)
        try:
            page.get_by_role("button", name="发布").first.click()
            page.wait_for_selector("text=确认发布", timeout=30000)
            page.get_by_role("button", name="确认发布").last.click()
            print("已点确认发布，等待结果…")
            for _ in range(48):
                page.wait_for_timeout(2500)
                body = page.inner_text("body")
                if "发布成功" in body or "你的游戏已发布" in body or "已发布到网页" in body:
                    print("发布成功")
                    break
                if "发布失败" in body or "冲突" in body:
                    die("发布被拒：" + body[-300:])
            ctx.storage_state(path=args.state)
        finally:
            ctx.close()
            browser.close()


def cmd_verify(args):
    state = json.loads(Path(args.state).read_text())
    cookie = next(c for c in state["cookies"] if c["name"] == "sb-rls-auth-token")
    request = urllib.request.Request(f"{ORIGIN}/api/gamefy/{args.game_id}",
                                     headers={"Cookie": "sb-rls-auth-token=" + cookie["value"],
                                              "User-Agent": UA})
    payload = json.load(urllib.request.urlopen(request, timeout=30))
    code = payload["code"]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(payload["publishedAt"]) / 1000))
    print(f"线上版本时间: {stamp} | 平台记录的字节: {payload['publishedBytes']} | 取回长度: {len(code)}")
    missing = [marker for marker in (args.expect or "").split(",") if marker and marker not in code]
    if missing:
        die("线上代码缺少标记: " + ", ".join(missing))
    print("线上核验通过:", args.expect)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("login", "upload", "publish", "verify"):
        node = sub.add_parser(name)
        node.add_argument("--state", default="/tmp/gallery-owner-state.json")
        if name != "login":
            node.add_argument("--game-id", required=True)
        if name in ("upload", "verify"):
            node.add_argument("--html", required=(name == "upload"))
            node.add_argument("--expect", default="")
        if name == "upload":
            node.add_argument("--expect-tabs", default="",
                              help="预览里分区标签的精确序列，逗号分隔，例如 立绘,日常,美图,脑洞,私密")
            node.add_argument("--preview-private", action="store_true",
                              help="发布前在工坊预览里核验私密相册的缩略图真的加载出来")
        if name == "publish":
            node.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    {"login": cmd_login, "upload": cmd_upload, "publish": cmd_publish, "verify": cmd_verify}[args.action](args)


if __name__ == "__main__":
    main()
