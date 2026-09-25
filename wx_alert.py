"""统一告警通道（飞书）。微信已弃用。

用法：
    from wx_alert import send_alert     # 推荐新名
    from wx_alert import send_weixin    # 兼容历史调用点

说明：send_weixin 这个名字是为了兼容历史调用点而保留的别名，
内部已全部走飞书（feishu_alert.send_feishu）。微信（ilinkai）逻辑已删除。
"""
from feishu_alert import send_feishu


def send_weixin(text: str, retries: int = 2) -> bool:
    """发一条告警（飞书）。兼容旧函数名。"""
    return send_feishu(text, retries)


def send_alert(text: str, retries: int = 2) -> bool:
    """发一条告警（飞书）。新代码推荐用这个名字。"""
    return send_feishu(text, retries)


if __name__ == "__main__":
    ok = send_alert("🤖 [告警通道自测] 飞书主通道正常")
    print("发送结果:", "✅ 成功" if ok else "❌ 失败")
