"""公演（P8 第 4 步从 bot.py 搬出）。

公演是这一整套里**唯一会用后台任务续跑**的玩法：`_perf_end_bg` 在结束广播之后
继续生成小说，所以它跨了"主循环结束"这个边界 —— 搬的时候要保证
后台任务里引用的名字全都还在（这正是迁移工具的静态依赖检查在防的事）。
"""
import asyncio
import random
import time
from datetime import datetime, timedelta

from brains import (generate_perf_comment, generate_perf_ending,
                    generate_perf_enrich, generate_perf_insult,
                    generate_perf_novel, generate_perf_novel_chunked,
                    generate_person_summary)
from dzmm import get_messages_all, get_messages_since, get_user
from wx_alert import send_weixin
from runtime import ctx
from runtime.group_state import _get_perf_state, _set_perf_state
from runtime.heat import _add_heat
from runtime.identity import (get_full_name, get_name, is_self, self_names,
                              self_names_longest_first)
from runtime.members import (_extract_msgs, _find_uid_by_name,
                             _find_user_in_msgs, _random_member_uid)
from runtime.novel import _extract_novel_title, _novel_save_path
from runtime.obs import log
from runtime.text import _is_stop_word, _squash
import state_store


__all__ = [
    "_detect_perf_query", "summarize_person_perf", "is_perf_start_cmd",
    "is_perf_end_cmd", "_get_perf_content", "_get_perf_content_full",
    "_perf_heat_target", "_perf_m_name", "_perf_send", "_handle_perf_start",
    "_perf_end_bg", "_handle_perf_end", "_check_perf_interact", "OWNER_UID",
]


OWNER_UID = "a56cbd67-3a1e-439a-8beb-d1557dbeff79"  # 糯糯～（群主，可代注册）


def _detect_perf_query(at_msg: str):
    """检测 @ 消息是否是"谁表现怎么样"类请求，返回 (目标名, 是否今天) 或 None."""
    triggers = ("表现怎么样", "表现如何", "表现咋样", "最近怎么样", "今天怎么样",
                "最近表现", "今天表现", "最近咋样", "表现")
    if not any(t in at_msg for t in triggers):
        return None
    today = ("今天" in at_msg) and ("最近" not in at_msg)
    name = at_msg
    # 长名优先 —— 名字可能互为前缀，短名先剥会留下残余（见 runtime/identity.py）
    for n in self_names_longest_first():
        if n:
            name = name.replace("@" + n, "")
    for t in triggers:
        name = name.replace(t, "")
    for w in ("今天", "最近", "的", "了", "啊", "呀", "呢", "吗", "嘛", "么",
              "怎么样", "如何", "咋样", "表现", "天气", "温度", "下雨", "下雪", "晴", "阴",
              "？", "?", "！", "!", " ", "～", "~", "，", "。", "、"):
        name = name.replace(w, "")
    name = name.strip()
    if not name:
        return None
    return name, today


def summarize_person_perf(target_name: str, today: bool) -> str:
    """按人总结表现（正常小小糯人设，不毒舌）."""
    msgs = get_messages_all(ctx.get_active_cid(), max_count=200)
    uid, full = _find_user_in_msgs(target_name, msgs)
    if not uid:
        return "没找到" + target_name + "这个人哦～"
    if today:
        since = datetime.now().strftime("%Y-%m-%d") + "T00:00:00Z"
        their = _extract_msgs(uid, get_messages_since(ctx.get_active_cid(), since, max_count=1000))
        label = "今天"
    else:
        their = _extract_msgs(uid, get_messages_all(ctx.get_active_cid(), max_count=300))
        label = "最近"
    if not their:
        return full + label + "都没怎么说话呢～"
    text = "\n".join(their[-50:])
    try:
        summary = generate_person_summary(full, text, label)
        return (summary or "").strip().replace("🤖", "")
    except Exception:
        return full + label + "聊得挺热闹的，不过我这边整理得有点乱～"


def is_perf_start_cmd(cmd: str) -> bool:
    return cmd.startswith("/公演开始")


def is_perf_end_cmd(cmd: str) -> bool:
    """识别公演结束：/公演结束、停止、停下、结束 等."""
    return cmd.startswith("/公演结束") or _is_stop_word(cmd)


def _get_perf_content(cid: str, start_id: str) -> str:
    """获取公演内容（旧→新）：从 /公演开始 之后到 /公演结束，只保留用户原始对话，跳过指令与 bot 消息."""
    msgs = get_messages_all(cid, max_count=1000)  # 旧→新
    lines = []
    start_i = 0
    for i, m in enumerate(msgs):
        if m.get("message_id") == start_id:
            start_i = i  # 从 /公演开始 本身开始（不回溯预热）
            break
    for m in msgs[start_i:]:
        uid = m.get("sent_by", "")
        if is_self(uid):
            continue  # 跳过 bot 消息（小小糯的丰富/总结），只保留用户原始对话
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text:
            continue
        if text.startswith("/"):
            if text.startswith("/公演结束"):
                break  # 到公演结束停止，不纳入结束后闲聊
            continue  # 跳过指令（/公演开始、/继续、/命令 等）
        lines.append(get_name(uid) + ": " + text)
    return "\n".join(lines)


def _get_perf_content_full(cid: str, start_id: str) -> str:
    """获取公演完整时间线（旧→新）：用户原始对话 + 小小糯丰富（标记【小小糯参考】），供小说择优整合."""
    msgs = get_messages_all(cid, max_count=1000)
    lines = []
    start_i = 0
    for i, m in enumerate(msgs):
        if m.get("message_id") == start_id:
            start_i = i
            break
    for m in msgs[start_i:]:
        uid = m.get("sent_by", "")
        text = (m.get("content") or {}).get("text", "").strip()
        if not text or "加入了群聊" in text:
            continue
        if is_self(uid):
            # 小小糯（bot）：去掉 🤖，排除非剧情（开场/排行/午安），合并多行为单行
            t = text.replace("🤖", "").strip()
            if any(kw in t for kw in ("🎬", "🔥", "排行", "午安", "公演开始")):
                continue
            if len(t) < 30:
                continue
            t = " ".join(t.split())  # 合并内部换行为单行，避免被当成多条对话
            lines.append("【小小糯参考】" + t)
            continue
        if text.startswith("/"):
            if text.startswith("/公演结束"):
                break
            continue
        lines.append(get_name(uid) + ": " + text)
    return "\n".join(lines)


def _perf_heat_target(cid: str) -> str:
    """识别公演里的被调教者（M方），返回 uid 或空."""
    perf = _get_perf_state(cid)
    content = _get_perf_content(cid, perf.get("start_id", ""))
    if not content:
        return ""
    # M方特征词（第一人称自我发情/认主/求饶，区别于S方的第二人称命令羞辱）
    m_kws = ["我发情", "我想要", "我湿", "我是", "我在", "汪汪", "求", "上头",
             "认主", "饶", "迫不及待", "干自己"]
    scores = {}
    for line in content.split("\n"):
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        text = line.split(":", 1)[1]
        hit = sum(1 for kw in m_kws if kw in text)
        if hit:
            scores[name] = scores.get(name, 0) + hit
    if not scores:
        return ""
    # 命中最多者 = M方；跳过糯糯（主持）和小小糯
    for name, _ in sorted(scores.items(), key=lambda kv: -kv[1]):
        uid = _find_uid_by_name(name)
        if uid and not is_self(uid) and uid != OWNER_UID:
            return uid
    return ""


def _perf_m_name(content: str) -> str:
    """从公演内容识别被调教方（M）的昵称（基于第一人称发情/求饶特征词，不拉消息）."""
    m_kws = ["我发情", "我想要", "我湿", "我是", "我在", "汪汪", "求", "上头",
             "认主", "饶", "迫不及待", "干自己"]
    scores = {}
    for line in content.split("\n"):
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        text = line.split(":", 1)[1]
        hit = sum(1 for kw in m_kws if kw in text)
        if hit:
            scores[name] = scores.get(name, 0) + hit
    if not scores:
        return ""
    return sorted(scores.items(), key=lambda kv: -kv[1])[0][0]


async def _perf_send(sender, text: str, cid: str, dry: bool):
    """发送公演消息（带 🤖 前缀）."""
    text = (text or "").strip()
    if not text:
        return
    if dry:
        log("[dry] " + text[:50])
        return
    ok = await sender.send("🤖 " + text, cid)
    log("✅" if ok else "❌")


PERF_TIMEOUT_SEC = 7200


def _check_perf_timeout(cid: str, timeout_sec: float = None) -> str:
    """公演超时自动结束。返回要发的文案；没超时返回 ""。

    ### 为什么必须补这个（原先**根本没有超时**）

    公演是五个玩法状态机里唯一没有超时的。而所有玩法都用
    "`active` 为真 → 该群其他指令一律不处理"做互斥，于是：

    - 公演开着而人散了（没人发 `/公演结束`）⇒ **这个群永久哑掉**，
      只有停止词那条分支还能走到；
    - 运维在页面上把"公演"关掉也救不回来 —— 状态还在，屏蔽照样生效。

    超时是"关掉功能能安全关闭"的前提（见 `runtime/features.py` 的复位设计）。
    这里只**解锁**：清掉 `active`，保留 `review_status`（待审的小说不能因此丢），
    **不主动生成小说**（那是 `/公演结束` 的流程，超时不该悄悄花一次 LLM 调用）。
    """
    perf = _get_perf_state(cid)
    if not perf.get("active"):
        return ""
    limit = float(PERF_TIMEOUT_SEC if timeout_sec is None else timeout_sec)
    ts = float(perf.get("last_interact_ts") or perf.get("started_ts") or 0)
    if ts and (time.time() - ts) < limit:
        return ""
    perf["active"] = False
    perf["timeout_ts"] = time.time()
    _set_perf_state(cid, perf)
    log("⏱️ [{}] 公演超时（{:.0f} 分钟无互动）自动解锁".format(
        (cid or "")[:8], limit / 60.0))
    return ""


async def _handle_perf_start(sender, cid: str, start_id: str, dry: bool):
    perf = {
        "active": True,
        "start_id": start_id,
        "last_interact_id": start_id,
        "last_interact_ts": time.time(),
        "interact_seq": 0,
        "review_status": "none",
    }
    _set_perf_state(cid, perf)
    await _perf_send(sender, "🎬 公演开始啦～姐姐们请开始表演，小小糯会实时丰富剧情、点评、羞辱哦～（结束发 /公演结束）", cid, dry)


async def _perf_end_bg(sender, cid: str, dry: bool) -> None:
    """后台跑公演收尾 + 小说生成（不堵主循环），异常只记日志."""
    try:
        await _handle_perf_end(sender, cid, dry)
        log("🎬 [" + cid[:8] + "] 公演收尾（后台）完成")
    except Exception as e:
        log("🎬 [" + cid[:8] + "] 公演收尾（后台）异常: " + str(e)[:100])


async def _handle_perf_end(sender, cid: str, dry: bool):
    perf = _get_perf_state(cid)
    start_id = perf.get("start_id", "")
    content = await asyncio.to_thread(_get_perf_content, cid, start_id)
    if not content:
        perf["active"] = False
        _set_perf_state(cid, perf)
        await _perf_send(sender, "公演内容为空，没法生成小说啦～", cid, dry)
        return

    # 1. 点评整个公演
    ending = await asyncio.to_thread(generate_perf_ending, content)
    await _perf_send(sender, "🎬 公演结束！小小糯点评：\n" + ending, cid, dry)

    # 1.5 等待参与者补充收尾（20秒，避免漏掉公演结束后的补充台词）
    await _perf_send(sender, "📝 想补充收尾的姐妹还有 20 秒哦～20 秒后我开始生成小说～", cid, dry)
    await asyncio.sleep(20)
    content = await asyncio.to_thread(_get_perf_content, cid, start_id)

    # 2. 提示生成中
    await _perf_send(sender, "📝 小说正在生成中，等糯糯姐审核后发送哦～", cid, dry)

    # 3. 生成小说（带角色标注，帮助 AI 识别 M 方）
    m_name = await asyncio.to_thread(_perf_m_name, content)
    novel_content = content
    if m_name:
        novel_content = "【被调教方（M）】：" + m_name + "\n\n" + content
    try:
        novel = (await asyncio.to_thread(generate_perf_novel_chunked, novel_content)).strip().replace("🤖", "")
    except Exception:
        novel = ""
    if not novel:
        perf["active"] = False
        _set_perf_state(cid, perf)
        await _perf_send(sender, "小说生成失败了，可以重新 /公演结束 再试哦～", cid, dry)
        return

    # 4. 保存
    path = _novel_save_path(cid, novel)
    with open(path, "w", encoding="utf-8") as f:
        f.write(novel)

    perf.update({
        "active": False,
        "novel_path": path,
        "novel_title": _extract_novel_title(novel),
        "review_status": "pending",
        "review_notified_ts": time.time(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _set_perf_state(cid, perf)
    await _perf_send(sender, "小说《" + perf["novel_title"] + "》已生成～请糯糯姐审核：/同意 发送｜/退回 重写｜/忽略 不再提醒", cid, dry)
    # 事件驱动：直连微信提醒糯糯审核（不等定时任务）
    send_weixin("📝【小小糯】公演小说《" + perf["novel_title"] + "》已生成，待审核～\n文件：" + path +
                "\n请在群里回复：/同意 发送｜/退回 重写｜/忽略 不再提醒")


def _check_perf_interact(cid: str, msgs: list) -> str:
    """公演进行中判断是否触发互动（只保留"丰富"）。触发条件：有新消息且距上次≥10分钟."""
    perf = _get_perf_state(cid)
    last_id = perf.get("last_interact_id", "")
    new_msgs = []
    found = False
    for m in msgs:  # 旧→新
        if not found:
            if m.get("message_id") == last_id:
                found = True
            continue
        uid = m.get("sent_by", "")
        if is_self(uid):
            continue
        text = (m.get("content") or {}).get("text", "").strip()
        if text and "加入了群聊" not in text:
            new_msgs.append(m)
    if not new_msgs:
        return ""

    # 触发条件：距上次互动 ≥ 10 分钟（600秒），且期间有新消息
    now = time.time()
    elapsed = now - perf.get("last_interact_ts", 0)
    if elapsed < 600:
        return ""

    perf["last_interact_id"] = msgs[-1]["message_id"]
    perf["last_interact_ts"] = now
    seq = perf.get("interact_seq", 0)
    perf["interact_seq"] = seq + 1
    _set_perf_state(cid, perf)

    # 增量喂料：本轮新增消息为主 + 前文一小段衔接
    new_text = "\n".join(
        get_name(m.get("sent_by", "")) + ": " + (m.get("content") or {}).get("text", "").strip()
        for m in new_msgs
    )
    content = _get_perf_content(cid, perf.get("start_id", ""))
    context = content[-200:] if content else ""
    tail = context + "\n\n【本轮新发生的内容】\n" + new_text

    # 记忆去重：把上一轮互动文本写进 prompt，要求避开
    prev_text = perf.get("last_interact_text", "")
    if prev_text:
        tail += "\n\n【上一轮小小糯已经说过的内容（务必避开重复）】\n" + prev_text

    reply = generate_perf_enrich(tail)
    perf["last_interact_text"] = reply
    _set_perf_state(cid, perf)
    return reply
