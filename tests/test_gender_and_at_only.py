"""P12-AL：称呼按性别 + 只有 @ 才回复。

屋主 2026-09-29：
  1. "不要叫谁都叫姐姐，根据性别叫。只不过**对女生温柔一点**。"
  2. "最后，**只有 @ 小小糯的时候才触发回复**。"

## 为什么"按性别叫"要单独改

基础提示词里"姐姐"出现 **13 次**、4 个示例**全是**"姐姐" —— 旧规则虽然写了
"女生叫姐姐、男生叫哥哥"，但示例与人格把模型训练成了一律叫姐姐（实测：对男生也叫姐姐）。
所以这次是**改示例 + 写死规则**，不是改一句称呼说明。
（性别从 `runtime.members._gender_label()` 来：本地档案直接读，查不到算"未知"，
@ 问答那条路会把它拼进对话：`名字（男生）`。）
"""
import ast
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brains                                                  # noqa: E402
import pytest                                                  # noqa: E402
from runtime import features as F                              # noqa: E402
from runtime import dispatch, features, mainloop, tuning       # noqa: E402

PROMPT = ROOT / "prompts" / "CMD_PROMPT.md"


def _t():
    return PROMPT.read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 按性别称呼

def test_prompt_teaches_gender_first_addressing():
    t = _t()
    assert "先看性别再决定怎么叫" in t
    assert "（女生）/（男生）/（未知）" in t
    assert '女生** → 叫"姐姐"' in t or '叫"姐姐"' in t
    assert '**男生** → 叫"哥哥"' in t
    assert "未知" in t and "别硬叫" in t
    # 明确点出最常犯的错
    assert "见谁都叫" in t and "姐姐" in t


def test_gentleness_is_reserved_for_girls():
    t = _t()
    assert "温柔是给女生的" in t
    # 对男生：可爱但不腻，且点名不许做的事
    assert "可爱但不腻" in t
    for bad in ("不撒娇", "不黏人"):
        assert bad in t


def test_few_shot_has_both_genders():
    """光靠规则不管用 —— 示例里必须有男生，否则模型照旧一律"姐姐"。"""
    t = _t()
    ex = t.split("示例：")[-1]
    assert "（男生）" in ex, "示例里要有男生的问答"
    assert "（女生）" in ex, "也要有女生的（做对照）"
    # 男生那条回答里不许叫姐姐
    male_line = [ln for ln in ex.split("\n") if ln.startswith("你:") and "哥哥" in ln]
    assert male_line, "男生示例的回答要用「哥哥」"
    assert "姐姐" not in male_line[0], "男生示例里不能出现「姐姐」"


def test_prompt_file_matches_code_default():
    assert _t() == brains.CMD_PROMPT, "文件与代码默认值必须逐字节一致"


# ---------------------------------------------------------------- ② 只 @ 才回

def test_at_only_registered_and_off_by_default():
    f = F.get("at_only")
    assert f.default is False, "默认关＝今天的行为（认证群靠非 @ 消息跑骰子/发骚/排行）"
    assert f.stage == "wired"
    assert "只 @" in f.label


def test_scan_loop_gates_everything_on_at_only():
    # P3-19 起主循环体在 runtime/mainloop.py（bot 桥 B 前缀，子串兼容）
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    i = src.index("P12-AL：`at_only` 的群里")
    seg = src[i:i + 1200]
    assert '_feat_on(cid, "at_only")' in seg
    assert "is_at_me(t, cid)" in seg
    assert "continue" in seg
    # 引用回复那条路（quote_reply）在 at_only 群里仍要能被放行（默认它是关的）
    assert '_quotes_self(m)' in seg


@pytest.mark.parametrize("cid,expect", [
    ("bd7fe6a4-0065-4551-ad3d-94116dba8f9c", False),   # 认证群：必须保持"能非 @ 触发"
    ("f4ec4c21-fb48-4be2-9972-e4128f65923b", False),   # 上班群同理
])
def test_at_only_is_not_on_for_the_busy_groups(cid, expect):
    assert F.on(cid, "at_only") is expect


# ---------------------------------------------------------------- ③ at_only 群的裸 /查 /注册

_ROSTER_CID = "ade37d32-900f-4cb8-9ce2-4272b93b82a5"   # Mr.橘的淫窝（at_only 群）


def _scan(cid, texts):
    """exec mainloop 真实的消息选择 for 循环（钉法同 test_dream._scan）。"""
    tree = ast.parse(inspect.getsource(mainloop))
    selection = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
                     and node.iter.id == "pending" and isinstance(node.target, ast.Name)
                     and node.target.id == "m"
                     and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                             and child.func.attr == "is_hash_request" for child in ast.walk(node)))
    bridge = SimpleNamespace(
        is_self=lambda uid: uid == "self", _feat_on=features.on, _tune=tuning.get,
        is_fortune_cmd=lambda t: False, is_at_me=lambda text, c: text.startswith("@小小糯"),
        _quote_reply_on=lambda c: False, _quotes_self=lambda message: False,
        _is_other_bot_noise=lambda c, text: False,
        _chat_cooling=lambda c, uid: 0, _is_room_trigger=lambda *a: False,
        _dispatch=dispatch)
    env = {"B": bridge,
           "_dream": SimpleNamespace(is_hash_request=lambda c, t: False),
           "_rose_moderation": mainloop._rose_moderation,
           "_tavern": mainloop._tavern,
           "_gallery": mainloop._gallery, "_office": mainloop._office,
           "_game_cards": mainloop._game_cards,
           "_vote": mainloop._vote,
           "_imagegen": mainloop._imagegen,
           "_videogen": mainloop._videogen,
           "_prompt_optimizer": mainloop._prompt_optimizer,
           "cid": cid, "log": lambda *a: None,
           "pending": [{"message_id": str(i), "sent_by": "guest", "content": {"text": x}}
                       for i, x in enumerate(texts)],
           "cmd": "", "cmd_sender": "", "at_msg": "", "at_sender": "",
           "_cmd_msg": None, "_at_msg_obj": None, "_room_obj": None,
           "_via_quote": False, "room_hit": None, "_leftover": []}
    exec(compile(ast.Module(body=[selection], type_ignores=[]), "<sel>", "exec"), env)
    return env


@pytest.mark.parametrize("text", [
    "/查 片姐", "/whois 片姐", "/资料 片姐", "/注册", "/register",
])
def test_roster_slash_cmds_pass_at_only_gate_when_enabled(text):
    """at_only + slash_cmds 群：裸 /查 /注册（没 @）必须进指令分支，不能被静默吞掉。"""
    features.set_overrides({_ROSTER_CID + "|at_only": True,
                            _ROSTER_CID + "|slash_cmds": True,
                            _ROSTER_CID + "|rose_moderation": False})
    try:
        assert _scan(_ROSTER_CID, [text])["cmd"] == text
    finally:
        features.set_overrides({})


@pytest.mark.parametrize("text", [
    "/开始摸鱼躲藏", "/记忆考核", "/答案 3", "/运势", "你好呀",
])
def test_other_slash_and_plain_text_still_swallowed_in_at_only(text):
    """白名单外的斜杠（别家 bot 的游戏语法）和不带 @ 的闲聊照旧静默。"""
    features.set_overrides({_ROSTER_CID + "|at_only": True,
                            _ROSTER_CID + "|slash_cmds": True})
    try:
        assert not _scan(_ROSTER_CID, [text])["cmd"]
    finally:
        features.set_overrides({})


def test_roster_cmd_still_blocked_when_slash_cmds_off():
    """slash_cmds 总闸关闭时（顶层茶水间）：即使是 /查 也不进指令，行为不变。"""
    features.set_overrides({_ROSTER_CID + "|at_only": True,
                            _ROSTER_CID + "|slash_cmds": False})
    try:
        assert not _scan(_ROSTER_CID, ["/查 片姐"])["cmd"]
    finally:
        features.set_overrides({})


# ---------------------------------------------------------------- ④ 酒馆指令闸门（P12-CG）

def test_tavern_cmd_passes_at_only_gate_when_enabled():
    """at_only + slash_cmds=off 的群里，酒馆指令（功能开着）必须进指令分支。"""
    cid = "13a7287a-2ec6-419e-9646-7f30a5154e6c"   # 瑞雯の酒馆
    features.set_overrides({cid + "|at_only": True, cid + "|slash_cmds": False,
                            cid + "|tavern_magic": True, cid + "|tavern_mute": True})
    try:
        assert _scan(cid, ["#除你衣物 张三"])["cmd"] == "/除你衣物 张三"
        assert _scan(cid, ["#小嘴巴闭起来 10分钟"])["cmd"] == "/小嘴巴闭起来 10分钟"
    finally:
        features.set_overrides({})


def test_tavern_cmd_swallowed_when_feature_off():
    """功能关着：酒馆指令跟其他 /xxx 一样静默（别吞别家 bot 的语法）。"""
    cid = "13a7287a-2ec6-419e-9646-7f30a5154e6c"
    features.set_overrides({cid + "|at_only": True, cid + "|slash_cmds": False})
    try:
        assert not _scan(cid, ["#除你衣物 张三"])["cmd"]
    finally:
        features.set_overrides({})


def test_ruiwen_hash_migration_covers_general_tavern_and_management_commands():
    cid = mainloop._tavern.RUIWEN_CID
    flags = {"at_only": True, "slash_cmds": True, "query": True, "register": True,
             "tavern_magic": True, "tavern_mute": True, "rose_moderation": True}
    old = features.overrides()
    features.set_overrides({cid + "|" + key: value for key, value in flags.items()})
    try:
        for text in ("查 张三", "注册", "荧光闪烁", "小嘴巴闭起来 10分钟", "恢复如初 张三"):
            assert _scan(cid, ["#" + text])["cmd"] == "/" + text
            assert not _scan(cid, ["/" + text])["cmd"]
        for text in ("阿瓦达啃大瓜 张三", "确认", "取消"):
            assert _scan(cid, ["＃" + text])["cmd"] == "#" + text
            assert not _scan(cid, ["/" + text])["cmd"]
        assert not _scan(cid, ["#随手写个话题"])["cmd"]
        assert not _scan(cid, ["／查 张三 @小小糯"])["at_msg"]
    finally:
        features.set_overrides(old)


def test_roster_whitelist_kept_in_sync_with_dispatch_matchers():
    """闸门白名单前缀必须与 dispatch 的两个匹配器保持一致（防漂移）。"""
    src = (ROOT / "runtime" / "mainloop.py").read_text(encoding="utf-8")
    seg = src[src.index("P12-CA"):src.index("P12-CA") + 700]
    for prefix in ("/查", "/whois", "/资料", "/注册", "/register"):
        assert prefix in seg, prefix


def test_game_cards_only_enter_command_queue_in_tea_room():
    tea = mainloop._game_cards.TEA_CID
    other = _ROSTER_CID
    saved = features.overrides()
    features.set_overrides({f"{cid}|{feature}": value for cid in (tea, other)
                            for feature, value in (("at_only", True), ("slash_cmds", False))})
    try:
        selected = _scan(tea, ["＃uNo", "#摸鱼传说", "#魔塔"])
        assert selected["cmd"] == "#uNo"
        assert [m["content"]["text"] for m in selected["_leftover"]] == ["#摸鱼传说", "#魔塔"]
        assert not _scan(other, ["#UNO", "#摸鱼传说", "#魔塔"])["cmd"]
        assert not _scan(tea, ["#UNO 开局", "#魔塔攻略", "/摸鱼传说"])["cmd"]
    finally:
        features.set_overrides(saved)


def test_tea_vote_hash_commands_pass_mention_gate_only_when_enabled(state):
    cid = mainloop._vote.TEA_CID
    saved = features.overrides()
    gates = {cid + "|at_only": True, cid + "|slash_cmds": False,
             _ROSTER_CID + "|at_only": True, _ROSTER_CID + "|slash_cmds": False}
    try:
        features.set_overrides({**gates, cid + "|vote": True})
        for text in ("＃投票 晚饭", "#选 1", "#投票结束"):
            assert _scan(cid, [text])["cmd"] == "#" + text[1:]
            assert not _scan(_ROSTER_CID, [text])["cmd"]
        assert not _scan(cid, ["#1", "#投票箱"])["cmd"]
        mainloop._vote._set_vote_state(cid, {"active": True})
        assert _scan(cid, ["#1"])["cmd"] == "#1"
        features.set_overrides({**gates, cid + "|vote": False})
        assert not _scan(cid, ["#投票 晚饭", "#选 1", "#投票结束", "#1"])["cmd"]
    finally:
        features.set_overrides(saved)


@pytest.mark.parametrize("prefix", ["#", "＃"])
@pytest.mark.parametrize("payload", ["", " 第二天"])
@pytest.mark.parametrize("action", ["继续", "结束", "潜规则结束", "潜规则"])
def test_tea_short_continue_bypasses_at_only_gate(prefix, payload, action):
    cid = mainloop._office.TEA_CID
    saved = features.overrides()
    features.set_overrides({cid + "|at_only": True, cid + "|slash_cmds": False,
                            cid + "|office": True})
    try:
        selected = _scan(cid, [prefix + action + payload])
        assert selected["cmd"] == "#" + action + payload
        assert selected["_cmd_msg"]["content"]["text"] == prefix + action + payload
    finally:
        features.set_overrides(saved)


@pytest.mark.parametrize("prefix", ["/", "／"])
def test_imagegen_without_at_and_multiple_commands_are_not_lost(prefix):
    cid = mainloop._imagegen.PRIMARY_CID
    saved = features.overrides()
    features.set_overrides({cid + "|imagegen": True, cid + "|at_only": True,
                            cid + "|slash_cmds": True})
    try:
        selected = _scan(cid, [prefix + "生图 橘猫", "/生图 水彩"])
        assert selected["cmd"] == "/生图 橘猫"
        assert len(selected["_leftover"]) == 1
        features.set_overrides({cid + "|imagegen": False})
        assert not _scan(cid, ["/生图 橘猫"])["cmd"]
        features.set_overrides({cid + "|imagegen": True, cid + "|slash_cmds": False})
        assert not _scan(cid, ["/生图 橘猫"])["cmd"]
    finally:
        features.set_overrides(saved)


@pytest.mark.parametrize("prefix", ["/", "／"])
def test_videogen_without_at_and_multiple_commands_are_not_lost(prefix):
    cid = mainloop._videogen.PRIMARY_CID
    saved = features.overrides()
    features.set_overrides({cid + "|videogen": True, cid + "|at_only": True,
                            cid + "|slash_cmds": True})
    try:
        selected = _scan(cid, [prefix + "生视频 橘猫", "/生视频 水彩"])
        assert selected["cmd"] == "/生视频 橘猫"
        assert len(selected["_leftover"]) == 1
        features.set_overrides({cid + "|videogen": False})
        assert not _scan(cid, ["/生视频 橘猫"])["cmd"]
        features.set_overrides({cid + "|videogen": True, cid + "|slash_cmds": False})
        assert not _scan(cid, ["/生视频 橘猫"])["cmd"]
    finally:
        features.set_overrides(saved)


@pytest.mark.parametrize("prefix", ["/", "／"])
def test_prompt_optimizer_without_at_respects_gates_and_keeps_second_command(prefix):
    cid = mainloop._prompt_optimizer.PRIMARY_CID
    saved = features.overrides()
    try:
        features.set_overrides({cid + "|prompt_optimize": True, cid + "|at_only": True,
                                cid + "|slash_cmds": True})
        selected = _scan(cid, [prefix + "提示词 视频 橘猫", "/提示词 视频 星空"])
        assert selected["cmd"] == "/提示词 视频 橘猫" and len(selected["_leftover"]) == 1
        features.set_overrides({cid + "|prompt_optimize": False})
        assert not _scan(cid, ["/提示词 视频 猫"])["cmd"]
        features.set_overrides({cid + "|prompt_optimize": True, cid + "|slash_cmds": False})
        assert not _scan(cid, ["/提示词 视频 猫"])["cmd"]
    finally:
        features.set_overrides(saved)
