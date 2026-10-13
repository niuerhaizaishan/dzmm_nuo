# -*- coding: utf-8 -*-
"""缺席名册 /寻人（P12-CB，2026-10-13 屋主需求）钉点测试。

钉的不是实现细节，是行为契约：
  · 指令识别三态 + 前缀清单防漂移；
  · 判定口径：15 天边界（整点两侧各 2 分钟）、活跃门槛（4 条不上榜 5 条上榜）、
    成员表判退群/潜水、成员表失败要注明而不是装知道；
  · 列表：最久缺席在前、最后一句话跳过 /# 指令、40 字截断、max_show 折行；
  · 单人卡：改名可按旧名查到、多候选绝不取第一个、门槛不足说人话、
    语录取最长 3 条、AI 小传失败/截断不拦卡片；
  · dispatch 接线：功能关 = 静默（归属表认领，不落兜底聊天）、开 = 真进分支；
  · tuning 三级回退：本群覆盖 absent.days 能改变判定口径。
"""
import asyncio
import json
import time

import pytest

import brains
import dzmm
import profiles
from prompt_defaults import PD
from runtime import dispatch as D
from runtime import features as F
from runtime import tuning
from runtime.absent import (CMD_PREFIXES, MEMOIR_FALLBACK, absent_reply,
                            is_absent_cmd)
from runtime.chat_archive import append_batch
from runtime.ctx import PRIMARY_CID

DAY = 86400.0


def forbidden(*args, **kwargs):
    raise AssertionError("不该走到这里：{} {}".format(args[:1], kwargs))


@pytest.fixture(autouse=True)
def _clean_switches(monkeypatch):
    """功能/参数覆盖一律空表起步：测试只钉注册表默认值的行为。"""
    monkeypatch.delenv("DZMM_FEATURES", raising=False)
    monkeypatch.delenv("DZMM_TUNING", raising=False)
    F.set_overrides({})
    tuning.set_overrides({})
    yield
    F.set_overrides({})
    tuning.set_overrides({})


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """临时归档库 + 干净的 profiles。返回 seed() 用来灌历史消息。"""
    monkeypatch.setenv("DZMM_CHAT_ARCHIVE_DB", str(tmp_path / "archive.db"))
    monkeypatch.setattr(profiles, "load_profiles", lambda: {})

    def seed(msgs, base=None):
        base = time.time() if base is None else base
        rows = []
        for mid, uid, name, days_ago, text in msgs:
            rows.append({"id": mid, "uid": uid, "name": name,
                         "sent_at": base - days_ago * DAY,
                         "text": text, "type": "text"})
        res = append_batch(rows, group_id=PRIMARY_CID, source="historical_import")
        assert res["invalid"] == 0 and res["inserted"] == len(rows), res

    return seed


def set_members(monkeypatch, uids, boom=False):
    """钉 chatroom.getMembers：uids=当前在群的人。boom=True 模拟拉表失败。"""
    def fake_api(method, payload):
        if boom:
            raise RuntimeError("成员表挂了")
        assert method == "chatroom.getMembers", method
        return {"members": [{"id": u, "isBot": False} for u in uids]}
    monkeypatch.setattr(dzmm, "api", fake_api)


def set_llm(monkeypatch, reply="她是最早那批爱说话的姐姐之一。", finish="stop"):
    seen = {}

    def fake(prompt, payload, max_tokens=None, timeout=None):
        seen["prompt"] = prompt
        seen["payload"] = json.loads(payload)
        seen["max_tokens"] = max_tokens
        seen["timeout"] = timeout
        if isinstance(reply, Exception):
            raise reply
        return reply, finish

    monkeypatch.setattr(brains, "_call_llm_raw", fake)
    return seen


# ---------------------------------------------------------------- 指令识别

def test_cmd_three_states():
    assert is_absent_cmd("/寻人") == ""
    assert is_absent_cmd("/缺席") == ""
    assert is_absent_cmd("/寻人 裳裳") == "裳裳"
    assert is_absent_cmd("/寻人   裳裳  ") == "裳裳"
    assert is_absent_cmd("/查 裳裳") is None
    assert is_absent_cmd("寻人") is None
    assert is_absent_cmd("") is None


def test_cmd_prefix_list_is_pinned():
    """前缀清单是契约的一部分：加了新前缀必须连测试一起改。"""
    assert CMD_PREFIXES == ("/寻人", "/缺席")


# ---------------------------------------------------------------- 数据底座

Q1 = "今天的风好大呀，把我的帽子都吹跑了，头发全乱了"          # 20 字
Q2 = "昨天看的电影太好看了，强烈推荐给大家"                    # 18 字
Q3 = "周末去吃了新开的火锅店，排了两小时"                      # 16 字


def alice_msgs(base):
    """爱丽丝：31 天前到 40 天前的 31 条文本（含 #、✧ 卡、/ 指令各 1 条）。"""
    msgs = [("a{:02d}".format(i), "u-alice", "爱丽丝",
             45 - i * 0.15, "日常{}".format(i)) for i in range(1, 25)]
    msgs += [("a25", "u-alice", "爱丽丝", 41.0, Q3),
             ("a26", "u-alice", "爱丽丝", 40.7, Q2),
             ("a27", "u-alice", "爱丽丝", 40.4, Q1),
             ("a28", "u-alice", "爱丽丝", 40.05, "大家再见，我有空回来看你们"),
             ("a29", "u-alice", "爱丽丝", 40.03, "#冒个泡"),
             ("a30", "u-alice", "爱丽丝", 40.02, "✧ 2026-08-09 运势 ✧\n糯糯，冰晶凝辉"),
             ("a31", "u-alice", "爱丽丝", 40.0, "/潜水去了")]
    return msgs


# ---------------------------------------------------------------- 裸列表

def test_list_judgement_order_groups_and_last_words(archive, monkeypatch):
    archive(alice_msgs(time.time()))
    # 小波：20 天前留下 60 个字（验证 40 字截断）；在群 → 💤
    archive([("b{:02d}".format(i), "u-bob", "小波", 20 + (10 - i) * 0.3, "字" * 60)
             for i in range(1, 11)], base=time.time())
    # 小夏 10 天前还在说 → 不缺席；潜水妹只冒过 4 个泡 → 门槛外；
    # 十五天整 ±2 分钟 → 边界两侧各一人（两边都要够 5 条门槛）；lily1 已退、lily2 在潜水
    archive([("c01", "u-carol", "小夏", 10.0, "我还常来哦"),
             ("c02", "u-carol", "小夏", 11.0, "日常水群")], base=time.time())
    archive([("d{:02d}".format(i), "u-dave", "潜水妹", 40 - i * 0.5, "冒泡{}".format(i))
             for i in range(1, 5)], base=time.time())
    archive([("e01", "u-erin", "十五天整", 20.0, "最早冒泡"),
             ("e02", "u-erin", "十五天整", 19.0, "第二条"),
             ("e03", "u-erin", "十五天整", 18.0, "第三条"),
             ("e04", "u-erin", "十五天整", 17.0, "第四条"),
             ("e05", "u-erin", "十五天整", 16.0, "第五条"),
             ("e06", "u-erin", "十五天整", 15.0025, "我去出差啦")], base=time.time())
    archive([("f01", "u-faith", "刚走不久", 16.0, "常驻人口"),
             ("f02", "u-faith", "刚走不久", 15.0 - 0.0025, "我还在的")], base=time.time())
    archive([("l1{:02d}".format(i), "u-lily1", "小美", 31 + (10 - i) * 0.2, "我是大美{}".format(i))
             for i in range(1, 11)], base=time.time())
    archive([("l2{:02d}".format(i), "u-lily2", "小美", 29 + (10 - i) * 0.2, "我是二美{}".format(i))
             for i in range(1, 11)], base=time.time())
    set_members(monkeypatch, ["u-bob", "u-carol", "u-grace", "u-erin",
                              "u-faith", "u-lily2", "u-dave"])
    # 格蕾丝改过名：归档里是旧名，档案里是现名 —— 按 uid 聚合、显示现名
    archive([("g{:02d}".format(i), "u-grace", "格蕾丝小时候的名字",
              26 + (20 - i) * 0.2, "格蕾丝的发言{}".format(i)) for i in range(1, 21)],
            base=time.time())
    monkeypatch.setattr(profiles, "load_profiles",
                        lambda: {"u-grace": {"name": "格蕾丝"}})

    out = absent_reply(PRIMARY_CID, "")

    assert "🕯️ 缺席名册 · 超过 15 天没说话的熟面孔（6 人）" in out
    assert out.index("⚰️ 已离开群聊") < out.index("💤 还在群里潜水")
    # 顺序 = 最久没说话在前：40 天 → 31 → 29 → 26 → 20 → 15
    order = [out.index("· {} — {} 天前".format(n, d))
             for n, d in (("爱丽丝", 40), ("小美", 31), ("小美", 29),
                          ("格蕾丝", 26), ("小波", 20), ("十五天整", 15))]
    assert order == sorted(order), order
    # 最后一句话跳过 / 与 # 开头，落到最后一条干净发言
    assert "· 爱丽丝 — 40 天前：“大家再见，我有空回来看你们”" in out
    # 40 字截断 + 省略号
    assert "“" + "字" * 40 + "…”" in out and "字" * 41 not in out
    # 分组：lily1 不在成员表 → ⚰️；lily2 在 → 💤
    assert "· 小美 — 31 天前：“我是大美10”" in out
    assert "· 小美 — 29 天前：“我是二美10”" in out
    # 门槛外与窗口内的人不上榜
    assert "潜水妹" not in out and "小夏" not in out and "刚走不久" not in out
    assert out.rstrip().endswith("想看谁留下的故事：/寻人 昵称")


def test_list_boundary_exactly_15_days(archive, monkeypatch):
    archive([("x01", "u-x", "边界妹", 15.0 - 0.0025, "刚好压线"),
             ("x02", "u-x", "边界妹", 16.0, "常驻")], base=time.time())
    set_members(monkeypatch, ["u-x"])
    out = absent_reply(PRIMARY_CID, "")
    assert "没有失踪人口" in out


def test_list_min_msgs_threshold(archive, monkeypatch):
    archive([("m{:02d}".format(i), "u-m", "刚好五条", 40 - i * 0.5, "发言{}".format(i))
             for i in range(1, 6)], base=time.time())
    set_members(monkeypatch, ["u-m"])
    out = absent_reply(PRIMARY_CID, "")
    assert "刚好五条" in out


def test_list_max_show_truncation(archive, monkeypatch):
    archive([("s{:02d}-{:02d}".format(j, i), "u-s{:02d}".format(j),
              "老朋友{:02d}".format(j), 40 - i * 0.5, "发言{}".format(i))
             for j in range(1, 11) for i in range(1, 6)], base=time.time())
    set_members(monkeypatch, ["u-s{:02d}".format(j) for j in range(1, 11)])
    out = absent_reply(PRIMARY_CID, "")
    assert "…还有 2 人没列出来，想看谁就说 /寻人 昵称" in out


def test_list_member_api_failure_notes_openly(archive, monkeypatch):
    archive(alice_msgs(time.time()))
    archive([("b{:02d}".format(i), "u-bob", "小波", 25 + (10 - i) * 0.3, "字" * 60)
             for i in range(1, 11)], base=time.time())
    set_members(monkeypatch, [], boom=True)
    out = absent_reply(PRIMARY_CID, "")
    assert "成员表没拉到，暂时分不出退群还是潜水" in out
    assert "⚰️" not in out, "拉不到成员表就不许装知道谁退群了"
    assert "· 爱丽丝" in out and "· 小波" in out


def test_list_without_archive_is_honest(archive):
    out = absent_reply(PRIMARY_CID, "")
    assert "糯糯还没找到认证群的聊天归档" in out


# ---------------------------------------------------------------- 单人卡

def test_card_full_anatomy(archive, monkeypatch):
    archive(alice_msgs(time.time()))
    set_members(monkeypatch, ["u-bob"])                      # 爱丽丝不在 → ⚰️
    seen = set_llm(monkeypatch)

    out = absent_reply(PRIMARY_CID, "爱丽丝")

    assert "📮 寻人启事 · 爱丽丝" in out
    assert "⚰️ 已离开群聊（最后发言 40 天前）" in out
    assert "💬 最后一句话：“大家再见，我有空回来看你们”" in out
    assert "🗣️ 留言 31 条 · 活跃于 " in out
    assert "📜 留下来的话：" in out
    for q in (Q1, Q2, Q3):
        assert "· " + q in out
    assert "· 日常1" not in out, "短发言不该进语录"
    assert out.endswith("她是最早那批爱说话的姐姐之一。")
    # 小传喂给模型的证据与预算
    assert seen["prompt"] == brains.ABSENT_MEMOIR_PROMPT == PD["ABSENT_MEMOIR_PROMPT"]
    p = seen["payload"]
    assert p["name"] == "爱丽丝" and p["days_absent"] == 40
    assert p["total_messages"] == 31
    assert p["last_text"] == "大家再见，我有空回来看你们"
    assert p["quotes"] == sorted([Q1, Q2, Q3], key=len, reverse=True)
    assert p["messages"][0] == "日常1", "取样要按老→新"
    assert seen["max_tokens"] == 500 and seen["timeout"] == 45


def test_card_member_still_lurking(archive, monkeypatch):
    archive([("b{:02d}".format(i), "u-bob", "小波", 25 + (10 - i) * 0.3, "字" * 60)
             for i in range(1, 11)], base=time.time())
    set_members(monkeypatch, ["u-bob"])
    set_llm(monkeypatch)
    out = absent_reply(PRIMARY_CID, "小波")
    assert "💤 还在群里，就是很久没说话" in out


def test_card_findable_by_old_name_after_rename(archive, monkeypatch):
    archive([("g{:02d}".format(i), "u-grace", "格蕾丝小时候的名字",
              30 - i * 0.2, "格蕾丝的发言{}".format(i)) for i in range(1, 21)],
            base=time.time())
    monkeypatch.setattr(profiles, "load_profiles",
                        lambda: {"u-grace": {"name": "格蕾丝"}})
    set_members(monkeypatch, ["u-grace"])
    set_llm(monkeypatch)
    out = absent_reply(PRIMARY_CID, "格蕾丝小时候的名字")
    assert "📮 寻人启事 · 格蕾丝" in out, "找到人之后要用现在的名字称呼她"


def test_card_not_found(archive, monkeypatch):
    set_members(monkeypatch, [])
    set_llm(monkeypatch, forbidden)
    out = absent_reply(PRIMARY_CID, "查无此人")
    assert "翻不到" in out


def test_card_multiple_candidates_never_guess(archive, monkeypatch):
    archive([("l1{:02d}".format(i), "u-lily1", "小美", 30 - i * 0.2, "我是大美{}".format(i))
             for i in range(1, 11)], base=time.time())
    archive([("l2{:02d}".format(i), "u-lily2", "小美", 29 - i * 0.2, "我是二美{}".format(i))
             for i in range(1, 11)], base=time.time())
    set_members(monkeypatch, forbidden)
    set_llm(monkeypatch, forbidden)
    out = absent_reply(PRIMARY_CID, "小美")
    assert "叫「小美」的有多位" in out
    assert "（10条）" in out


def test_card_below_threshold_says_so(archive, monkeypatch):
    archive([("d{:02d}".format(i), "u-dave", "潜水妹", 40 - i * 0.5, "冒泡{}".format(i))
             for i in range(1, 5)], base=time.time())
    set_members(monkeypatch, forbidden)
    set_llm(monkeypatch, forbidden)
    out = absent_reply(PRIMARY_CID, "潜水妹")
    assert "只在群里冒过 4 个泡" in out and "还没到名册门槛（5 条）" in out


@pytest.mark.parametrize("bad", [RuntimeError("模型炸了"), " length 截断 ", ("", "stop")],
                         ids=["raise", "truncated", "empty"])
def test_card_memoir_failure_does_not_block_card(archive, monkeypatch, bad):
    archive([("b{:02d}".format(i), "u-bob", "小波", 25 + (10 - i) * 0.3, "字" * 60)
             for i in range(1, 11)], base=time.time())
    set_members(monkeypatch, ["u-bob"])
    if isinstance(bad, Exception):
        set_llm(monkeypatch, bad)
    elif isinstance(bad, tuple):
        set_llm(monkeypatch, reply=bad[0], finish=bad[1])
    else:
        set_llm(monkeypatch, reply=bad, finish="length")
    out = absent_reply(PRIMARY_CID, "小波")
    assert "📮 寻人启事 · 小波" in out and "字" * 40 in out
    assert MEMOIR_FALLBACK in out


# ---------------------------------------------------------------- tuning / 接线

def test_tuning_override_changes_judgement(archive, monkeypatch):
    archive([("n{:02d}".format(i), "u-n", "四天妹", 5 + (6 - i) * 0.5, "发言{}".format(i))
             for i in range(1, 7)], base=time.time())
    set_members(monkeypatch, ["u-n"])
    set_llm(monkeypatch, forbidden)
    assert "四天妹" not in absent_reply(PRIMARY_CID, ""), "默认 15 天口径下不该上榜"
    tuning.set_overrides({"{}|absent.days".format(PRIMARY_CID): 3})
    assert "四天妹" in absent_reply(PRIMARY_CID, ""), "本群覆盖要能改判定口径"


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, text, cid, **kwargs):
        self.sent.append((cid, text))
        return True


async def _slash(sender, cid, text):
    group_state, state = {"last_id": ""}, {}
    messages = [{"message_id": "req", "sent_by": "guest", "content": {"text": text}}]
    await D.handle_slash(sender, cid, text, "guest", False, group_state,
                         messages, messages[0], state,
                         _save_group=lambda *a: None, _mark_inflight=lambda *a: None,
                         _remember_turn=lambda *a: None,
                         rank_summary=forbidden, sync_profiles=forbidden)


@pytest.fixture
def entry_mocks(monkeypatch):
    monkeypatch.setattr(D, "log", lambda *a: None)
    monkeypatch.setattr(D, "generate_reply", forbidden)
    monkeypatch.setattr(D, "absent_reply", forbidden)
    for name in ("_get_perf_state", "_get_fasao_state", "_get_dj_state",
                 "_get_story_state", "_get_vote_state", "_dice_state"):
        monkeypatch.setattr(D, name, lambda *args: {})


def test_dispatch_disabled_is_silent_not_fallback(entry_mocks):
    """默认关：/寻人 要被归属表静默认领，绝不落到兜底聊天。"""
    assert D._cmd_owned_by_disabled(PRIMARY_CID, "/寻人") is True
    assert D._cmd_owned_by_disabled(PRIMARY_CID, "/缺席") is True
    sender = FakeSender()
    asyncio.run(_slash(sender, PRIMARY_CID, "/寻人"))
    assert sender.sent == []


def test_dispatch_enabled_reaches_branch(entry_mocks, monkeypatch):
    """按群打开后 /寻人（裸与带昵称）真进 absent 分支，回 🤖 前缀正文。"""
    F.set_overrides({PRIMARY_CID + "|absent": True})
    assert D._cmd_owned_by_disabled(PRIMARY_CID, "/寻人") is False
    seen = []

    def fake_absent(cid, query):
        seen.append((cid, query))
        return "名册正文"

    monkeypatch.setattr(D, "absent_reply", fake_absent)
    sender = FakeSender()
    asyncio.run(_slash(sender, PRIMARY_CID, "/寻人 裳裳"))
    assert seen == [(PRIMARY_CID, "裳裳")]
    assert sender.sent == [(PRIMARY_CID, "🤖 名册正文")]
    asyncio.run(_slash(sender, PRIMARY_CID, "/寻人"))
    assert seen[-1] == (PRIMARY_CID, ""), "裸指令 = 列表查询，query 必须是空串"
    assert len(sender.sent) == 2


def test_registry_uses_only_registered_keys():
    """归属表里的 key 必须在注册表里（拼错 = 静默失效）。"""
    from runtime.dispatch import _CMD_FEATURE_ORDER
    for keys, _pred, _gate in _CMD_FEATURE_ORDER:
        for k in keys:
            assert k in set(F.keys()), k
