"""admin 页面统一外壳（P4-4A）。

五个页面（总览 / 群与 Bot / 功能与参数 / 人设 / 提示词）共用：
  · `<head>` 注入共享三件套（base/components/dashboard.css）+ app.js（渐进增强）
  · `.app` 外壳：侧边栏（分组导航 + 运行状态点）+ sticky header（面包屑 + 主题切换）
  · 主题切换 / 侧边栏交互 / Toast 由 app.js 负责（无 JS 时纯表单仍可用）

各页只产出 **content 片段**，调 `page(...)` 得到完整文档——
页内不再各自内联 `_CSS`（样式漂移的根因）。
"""
import html

__all__ = ["page"]


# (路径, 图标, 标签, 分组) —— 路径必须是真实路由（不做死链）。
# 分组顺序 = 使用频率 × 风险：监控在前、高频配置居中、低频/账户置底。
_NAV = (
    ("监控", (
        ("/", "📊", "总览"),
        ("/messages", "💬", "群聊天记录"),
    )),
    ("运行时配置", (
        ("/features", "🎚️", "功能与参数"),
        ("/edit", "👥", "群与 Bot"),
        ("/personas", "🎭", "人设与话术"),
        ("/prompts", "📝", "提示词"),
    )),
    ("账户", (
        ("/logout", "🚪", "退出"),
    )),
)


def _nav_html(active: str) -> str:
    """渲染侧边栏导航；`active` = 当前路径（精确匹配高亮）。"""
    out = []
    for label, items in _NAV:
        out.append("<div class='nav-group'>")
        out.append("<div class='nav-group__label'>{}</div>".format(label))
        for path, icon, text in items:
            cls = "nav-item active" if path == active else "nav-item"
            href = "/" if path == "/" else path
            out.append(
                "<a class='{cls}' href='{href}'>"
                "<span class='nav-item__icon'>{icon}</span>"
                "<span class='nav-item__label'>{text}</span></a>".format(
                    cls=cls, href=html.escape(href), icon=icon, text=text))
        out.append("</div>")
    return "".join(out)


def page(*, active: str, title: str, breadcrumb: str, content: str,
         extra_head: str = "") -> str:
    """包一层统一外壳，返回完整 HTML 文档。

    `content` 是各页自产的 body 片段（消息块 + 表单 + 卡片），
    不含 <html>/<head>/<body> 与导航。
    `extra_head`：页面特有的少量 <style>/<script>（大头一律走共享 CSS，
    这里只允许页面真特有的样式——4A 的目标就是消灭漂移）。
    """
    return (
        "<!doctype html><html lang='zh' data-theme='dark'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>{title} · 小小糯 运维后台</title>"
        "<link rel='stylesheet' href='/static/base.css'>"
        "<link rel='stylesheet' href='/static/components.css'>"
        "<link rel='stylesheet' href='/static/dashboard.css'>"
        "{extra_head}"
        "<script src='/static/app.js' defer></script>"
        "</head><body>"
        "<div class='app'>"
        "<aside class='sidebar' id='sidebar'>"
        "<div class='sidebar__brand'><div class='sidebar__logo'>糯</div>"
        "<div class='sidebar__title'>小小糯 · 运维后台</div></div>"
        "<nav class='sidebar__nav'>{nav}</nav>"
        "<div class='sidebar__footer'>"
        "<span class='badge badge--ok badge--dot badge--pulse'>运行中</span>"
        "</div>"
        "</aside>"
        "<div class='main'>"
        "<header class='header'>"
        "<div class='header__left'>"
        "<button class='sidebar-toggle' id='sidebarToggle' "
        "aria-label='打开侧边栏'>☰</button>"
        "<span class='header__breadcrumb'>{crumb}</span>"
        "</div>"
        "<div class='header__right'>"
        "<button class='theme-toggle' id='themeToggle' aria-label='切换主题'>☀️</button>"
        "</div>"
        "</header>"
        "<div class='content'>{content}</div>"
        "</div>"
        "</div>"
        "<div class='sidebar-overlay' id='overlay'></div>"
        "</body></html>").format(
        title=html.escape(title), nav=_nav_html(active),
        crumb=html.escape(breadcrumb), content=content,
        extra_head=extra_head)
