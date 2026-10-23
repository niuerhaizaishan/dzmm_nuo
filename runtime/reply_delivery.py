"""只有确定尚未投递的失败才允许重试。"""


class ReplyNotSent(RuntimeError):
    """账号未连接，或平台明确拒绝；可以保留正文并有限重试。"""
