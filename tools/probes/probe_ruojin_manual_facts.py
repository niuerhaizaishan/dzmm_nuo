"""生成若瑾使用说明前的事实核对：这个群到底开了哪些功能、认哪些指令（只读）。"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/admin")

import bot                                                     # noqa: E402
import personas as P                                           # noqa: E402
from admin import dashboard                                    # noqa: E402
from runtime import config_store                               # noqa: E402
from runtime import features as F                              # noqa: E402

CID = "f4ec4c21-fb48-4be2-9972-e4128f65923b"

defaults = dashboard._config_defaults()
cfg = config_store.reload(defaults)
F.set_overrides(cfg.feature_map())
P.set_config_personas({s.cid: s.persona for s in cfg.group_specs.values()
                       if s.persona})

raw = json.load(open("/app/config/config.json", encoding="utf-8"))
g = [x for x in (raw.get("groups") or []) if str(x.get("cid", "")).startswith("f4ec4c21")][0]
print("=== 群条目 ===")
print("  alias={!r} enabled={} mute={} screen={} push={} persona={}".format(
    g.get("alias"), g.get("enabled"), g.get("mute"), g.get("screen"),
    g.get("push"), g.get("persona")))
print("  人设：", P.persona_for(CID)["name_full"], " orientation=",
      P.persona_for(CID).get("orientation"))

on, off = [], []
for k in F.keys():
    (on if F.on(CID, k) else off).append(k)
print()
print("=== 这个群开着的功能（{}）===".format(len(on)))
print("  " + "、".join(on))
print("=== 关着的功能（{}）===".format(len(off)))
print("  " + "、".join(off))

print()
print("=== 指令识别（实际会命中的写法）===")
samples = ["/羞辱 某人", "/骂 某人", "/diss 某人", "/损 某人", "/吐槽 某人",
           "/发骚 某人 自定义玩法", "/体位骰", "/场景骰", "/涩涩骰", "/发情点名 某人",
           "/继续", "/继续 换个角度", "/续写", "/重写", "/重来",
           "/结束", "/停止", "/发骚结束",
           "/调教 某人 学狗叫", "/调教开始 某人 爬过来", "/命令 跪好", "/跪好", "/调教结束",
           "/查 某人", "/whois 某人", "/资料 某人", "/注册", "/注册 某人",
           "/记住 xxx", "/回忆 xxx", "/说明", "/帮助", "/指南", "/自我介绍",
           "/同步", "/骚度排行", "/写诗", "/算命", "/投票 主题|A|B", "/剧情开始",
           "/吹牛骰子", "/公演开始", "/吐槽开", "/talk 妈妈", "/ask 天气"]
flags = [
    ("羞辱", lambda c: bot.is_insult_cmd(c)),
    ("发骚(scene)", bot.is_scene_cmd),
    ("发骚点名(fasao)", bot.is_fasao_cmd),
    ("继续", bot.is_continue_cmd),
    ("重写", lambda c: bot.is_rewrite_cmd(c)),
    ("结束词", bot.is_fasao_end_cmd),
    ("调教开始", lambda c: bot.is_diaojiao_cmd(c)),
    ("调教命令", lambda c: bot.is_dj_command_cmd(c)),
    ("调教结束", bot.is_dj_end_cmd),
    ("查人", bot.is_profile_cmd),
    ("注册", lambda c: bot.is_register_cmd(c)),
    ("记住", lambda c: bot.is_remember_cmd(c)),
    ("回忆", bot.is_recall_cmd),
    ("说明", lambda c: bot.is_help_cmd(c) or bot.is_guide_cmd(c)),
    ("自我介绍", bot.is_intro_cmd),
    ("同步", bot.is_sync_cmd),
    ("排行", bot.is_rank_cmd),
    ("写诗", lambda c: bot.is_poem_cmd(c)),
    ("算命", bot.is_fortune_cmd),
    ("投票", bot.is_vote_start_cmd),
    ("剧情", bot.is_story_start_cmd),
    ("骰子", lambda c: bot.is_dice_cmd(c) if hasattr(bot, "is_dice_cmd") else None),
]
for s in samples:
    hit = []
    for name, fn in flags:
        try:
            if fn(s):
                hit.append(name)
        except Exception:                                      # noqa: BLE001
            pass
    print("  {!r:28s} → {}".format(s, "、".join(hit) or "（谁都不认）"))
