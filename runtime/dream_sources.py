"""独立整理的古籍短引索引，不复制第三方 Skill 的代码或现代解释。

原文：《周公解夢》，传统古籍，原作属于公有领域。
转录核对：维基文库 https://zh.wikisource.org/wiki/周公解夢 ，
及 easoch9-spec/zhougong-dream-skill 的 references/zhougong-jiemeng.md
（blob 5c444b78eec5e448f3c5cf9178bd8261f2d87112），2026-10-04。
转录来源署名：维基文库贡献者、easoch9-spec/zhougong-dream-skill。
为保留转录来源的署名与相同方式共享要求，本文件的古籍转录短引采用
CC BY-SA 4.0：https://creativecommons.org/licenses/by-sa/4.0/ 。
改动：仅选取短句并独立添加简体检索条件；未复制全文或现代解读。
"""

SOURCES = (
    {"id": "sky_fly", "chapter": "天地日月星辰", "quote": "飛上天富貴大吉",
     "all": ("飞",), "any": ("天空", "天上", "上天"),
     "exclude": ("飞机", "坠", "摔", "掉", "飞不", "不能飞")},
    {"id": "rain_clear", "chapter": "天地日月星辰", "quote": "天晴雨散百憂去",
     "all": ("雨",), "any": ("天晴", "放晴", "雨停"),
     "exclude": ("没停", "不停", "不晴", "没有停")},
    {"id": "cloud_sun", "chapter": "天地日月星辰", "quote": "雲開日出凶事散",
     "all": ("云",), "any": ("太阳出来", "太阳露", "日出"),
     "exclude": ("看不见", "没出来", "没有出来")},
    {"id": "dry_tree_flower", "chapter": "地理山石樹木", "quote": "枯木開花子孫興",
     "all": (), "any": ("枯树开花", "枯木开花", "枯树又开花", "枯木又开花"),
     "exclude": ("没开花", "不开花", "没有开花")},
    {"id": "garden_green", "chapter": "地理山石樹木", "quote": "園林茂盛大吉利",
     "all": (), "any": ("园林茂盛", "花园茂盛", "花园郁郁葱葱"),
     "exclude": ("枯", "烧", "荒", "不茂盛")},
    {"id": "tree_climb", "chapter": "地理山石樹木", "quote": "登大樹名利顯揚",
     "all": (), "any": ("爬上大树", "爬到大树", "登上大树"),
     "exclude": ("摔", "掉", "断", "爬不上")},
)

_TRADITIONAL = str.maketrans("飛雲樹園夢見開陽來裡", "飞云树园梦见开阳来里")


def candidates(dream: str) -> tuple:
    text = "".join(dream.translate(_TRADITIONAL).split())
    hits = [item for item in SOURCES
            if all(word in text for word in item["all"])
            and any(word in text for word in item["any"])
            and not any(word in text for word in item["exclude"])]
    return tuple(hits[:3])
