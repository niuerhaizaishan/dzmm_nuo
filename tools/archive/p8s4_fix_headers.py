"""把 slice header 里**重复定义**的常量删掉（保持工具重跑结果与现状一致）。

9 个 header 在正文里又写了一遍常量，同时 consts=[...] 还从 bot.py 搬了一份。
正确做法是只留搬过来的那份；header 里改成一行说明。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
P = ROOT / "tools" / "archive" / "p8s4_move.py"
t = P.read_text(encoding="utf-8")

# (header 里要删掉的原文, 替换成的说明) —— 逐条精确匹配，任一条不中即中止
PATCHES = [
    ('''MEMORY_MAX = 100

__all__ = [
    "_get_fasao_state"''',
     '''# （MEMORY_MAX 等常量随搬运一起进来，见本 slice 的 consts）

__all__ = [
    "_get_fasao_state"'''),

    ('''FASAO_TIMEOUT = 15 * 60  # 发骚 active 超过 15 分钟无互动自动结束（防卡死）

SCENES = ["更衣室", "办公室", "教室", "浴室", "天台", "停车场", "公厕隔间", "深夜公园", "KTV包间", "楼梯间"]
POSITIONS = ["后入", "骑乘", "69", "口爆", "颜射", "捆绑", "spanking", "道具（跳蛋·振动棒·肛塞）", "项圈牵引", "露出", "强制高潮", "失禁play", "兽耳cos", "师生角色扮演", "主人与宠物", "双飞", "群P"]
EXTRAS = ["蒙眼", "手铐", "戴项圈", "不许出声", "公开play被围观", "边被玩边打电话", "塞着跳蛋出门", "让她求饶才停"]
XP_BIAS = {
    "小M": {"pos": ["后入", "口爆", "捆绑", "项圈牵引", "spanking", "失禁play"]},
    "被支配": {"pos": ["后入", "捆绑", "强制高潮", "项圈牵引"]},
    "小S": {"pos": ["骑乘", "颜射", "spanking", "强制高潮"]},
    "支配": {"pos": ["骑乘", "spanking", "强制高潮"]},
    "露出": {"scene": ["天台", "深夜公园", "停车场", "公厕隔间"], "extra": ["露出", "公开play被围观", "塞着跳蛋出门"]},
    "口交": {"pos": ["口爆", "69"]},
    "绳缚": {"pos": ["捆绑"], "extra": ["手铐", "戴项圈"]},
    "母狗化": {"extra": ["戴项圈", "让她求饶才停"]},
}

__all__ = [''',
     '''# （FASAO_TIMEOUT / SCENES / POSITIONS / EXTRAS / XP_BIAS 随搬运一起进来，
#   见本 slice 的 consts —— 在 header 里再写一遍会变成重复定义）

__all__ = ['''),

    ('''OWNER_UID = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"  # 糯糯～（群主，可代注册）

__all__ = [''',
     '''# （OWNER_UID 随搬运一起进来，见本 slice 的 consts）

__all__ = ['''),

    ('''VOTE_DEFAULT_SECONDS = 2 * 60  # 默认 2 分钟自动结束

__all__ = [''',
     '''# （VOTE_DEFAULT_SECONDS 随搬运一起进来，见本 slice 的 consts）

__all__ = ['''),

    ('''STORY_MAX_ROUND = 6
STORY_VOTE_SECONDS = 60

__all__ = [''',
     '''# （STORY_MAX_ROUND / STORY_VOTE_SECONDS 随搬运一起进来，见本 slice 的 consts）

__all__ = ['''),

    ('''DJ_PARTICIPATE_HINT = "想参与的姐妹也来玩呀～发 /命令 xxx 或直接 /xxx 都行（加不加「命令」都认）"

__all__ = [''',
     '''# （DJ_PARTICIPATE_HINT 随搬运一起进来，见本 slice 的 consts）

__all__ = ['''),

    ('''# 昵称缓存（uid -> 昵称），跨轮次复用
_name_cache: dict = {}

__all__ = [''',
     '''# （_name_cache 随搬运一起进来，见本 slice 的 consts）

__all__ = ['''),

    ('''HISTORY_MAX = 5             # 每人保留最近 5 版变化轨迹（姓名/签名/简介一起）

__all__ = [''',
     '''# （HISTORY_MAX 随搬运一起进来，见本 slice 的 consts）

__all__ = ['''),

    ('''# 群名缓存（cid -> {"name":..., "ts":...}），与 bot.py 的 get_group_name 共享
_group_name_cache: dict = {}

__all__ = [''',
     '''# （_group_name_cache 随搬运一起进来，见本 slice 的 consts）

__all__ = ['''),

    ('''# 繁→简转换器懒加载；opencc 不可用时为 False（跳过转换，不影响其它归一化）
_OPENCC_T2S = None

__all__ = [''',
     '''# （_OPENCC_T2S 随搬运一起进来，见本 slice 的 consts）

__all__ = ['''),
]

for i, (old, new) in enumerate(PATCHES):
    if t.count(old) != 1:
        print("!! 第 {} 条匹配 {} 次，中止".format(i, t.count(old)))
        print(repr(old[:70]))
        raise SystemExit(1)
    t = t.replace(old, new, 1)

P.write_text(t, encoding="utf-8")
print("已清理 {} 个 header".format(len(PATCHES)))
