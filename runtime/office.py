"""茶水间专用命令：双人剧情 + 付费概率玩法。

链路：解析（目标/加币/方向）→ 核验（成员、职级、性别、拒绝开关、额度）→ 算概率与费用
      → 查余额 → 预占会话与次数 → 抽一次（持久化）→ 扣币 → 生成（成功/失败两套文案）
      → 引用原指令投递 → 核对落地 → 成功给目标分账 → 结算。

钱的每一步都有幂等编号（见 office_store 的 KV_*）。钱包后端是适配层
（runtime/office_wallet.py）：没配就「不扣币、不抽取、不扣次数」，不会白嫖成功。
"""
import asyncio
import hashlib
import json
import re
import secrets
import shlex

import personas
import dzmm
from runtime import features
from runtime.employee_api import Client, EmployeeUnavailable
from runtime.identity import get_full_name, is_self
from runtime.members import _gender_of
from runtime.moderation_platform import Platform, ModerationUnavailable
from runtime.obs import log
from runtime import office_rules as rules
from runtime.office_scene import SceneGenerationError, generate, generate_failure
from runtime.office_store import (KV_CONT, KV_FEE, KV_REFUND, KV_SPLIT,
                                  RuleError, Store, TEA_CID)
from runtime.office_wallet import (UnavailableWallet, WalletRefused, WalletUnavailable,
                                   adapter as wallet_adapter)
from runtime.reply_delivery import ReplyContentRejected, ReplyNotSent
from runtime.text import _normalize_command_prefix

COMMAND = re.compile(
    r"^#(允许潜规则|拒绝潜规则|潜规则状态|潜规则规则|潜规则帮助|潜规则继续|潜规则结束|潜规则|继续|结束)"
    r"(?=$|\s)(.*)$", re.S)
TASKS = set()
_feat_on = features.on
USAGE = ("发送 #潜规则 目标 [加币] [剧情方向]，也可用工号，或引用对方消息发送 "
         "#潜规则 [加币] [剧情方向]；可跨部门，加币 0~50 币。")
NO_WALLET = "摸鱼币接口还没接上（需要余额/扣币/发币接口），现在不能收费开局，也不会扣币。"


def command(text):
    match = COMMAND.fullmatch(_normalize_command_prefix(text))
    if match is None:
        return None
    action = {"继续": "潜规则继续", "结束": "潜规则结束"}.get(match[1], match[1])
    return action, match[2].strip()


def enabled(cid):
    return cid == TEA_CID and personas.persona_key_for(cid) == "nuo" and _feat_on(cid, "office")


def reference_of(message):
    content = message.get("content") or {}
    return {"id": message.get("message_id", ""), "sentBy": message.get("sent_by", ""),
            "content": {"type": "text", "text": str(content.get("text") or "")[:2000]}}


def split_request(payload):
    """把「目标 加币 方向」拆开。工号（#0060）保留井号，带空格的名字用双引号。"""
    try:
        tokens = shlex.split(payload, posix=True)
    except ValueError:
        raise RuleError("目标格式不完整，请使用 @目标 或工号；带空格的名字可加双引号。") from None
    if not tokens:
        raise RuleError(USAGE)
    return tokens[0], tokens[1:]


async def quoted_target(cid, message):
    content = (message or {}).get("content") or {}
    quoted = next((content[key] for key in ("reference", "quote", "reply") if key in content), None)
    if quoted is None:
        return None
    if not isinstance(quoted, dict):
        raise RuleError("引用消息格式不完整，请重新引用对方的群消息再试。")
    mid = quoted.get("id") or quoted.get("message_id")
    if not isinstance(mid, str) or not mid.strip():
        raise RuleError("引用消息缺少编号，请重新引用对方的群消息再试。")
    stamp = quoted.get("sent_at") or quoted.get("sentAt") or ""
    try:
        original = await Platform().message(cid, mid, stamp if isinstance(stamp, str) else "")
    except ModerationUnavailable:
        raise EmployeeUnavailable("暂时无法核验引用的本群原消息，请重新引用后再试，不会扣币。") from None
    target = original.get("sent_by")
    if (original.get("message_id") != mid or original.get("chatroom_id") not in (None, cid)
            or not isinstance(target, str) or not target.strip()):
        raise RuleError("引用消息无法核验为本群成员的原消息，请重新引用后再试。")
    return target


def _platform_gender(cid, uid):
    """方案 §8 第 1 步：当前群平台资料里的 gender（user.getChatroomUser）。

    实测取值有 female / male / lesbian / gay / femboy / other；非二元标签按"未知"处理，
    不算"平台没给"，所以不会再去本地档案猜一次。
    """
    try:
        data = dzmm.api("user.getChatroomUser", {"json": {"chatroomId": cid, "userId": uid}},
                        timeout=5)
    except Exception:                                          # noqa: BLE001
        return ""
    return rules.normalize_gender((data or {}).get("gender"))


async def _gender(cid, uid):
    explicit = await asyncio.to_thread(_platform_gender, cid, uid)
    if explicit:
        return explicit                      # "未知" 也算平台给了值：不再回本地猜
    local = await asyncio.to_thread(_gender_of, uid, cid, False)
    return {"男生": "男", "女生": "女"}.get(local, "未知")


def eligibility(actor, target):
    """现在任何方向都能发起；只要求双方职级都能核验。"""
    if actor.get("rank_level") is None or target.get("rank_level") is None:
        raise RuleError("暂时无法判断双方的职级高低，请确认资料后再试，不会扣币。")


async def reply(sender, cid, text, reference, *, guard=None, strict=False, keep_lines=False):
    # 原生引用区分作者；正文也区分指令，避免多人收到同一提示被平台判为重复内容。
    name = " ".join(get_full_name(reference.get("sentBy", "")).split())[:24]
    request_id = hashlib.sha256(reference["id"].encode()).hexdigest()[:8]
    body = text if keep_lines else " ".join(text.split())
    payload = "🤖 【回复 {} · 指令 {}】 {}".format(name, request_id, body)
    try:
        return await sender.send_reply_with_id(payload, cid, reference, guard=guard)
    except Exception as error:
        log("office_reply 未确认 [{}] {}".format(cid[:8], type(error).__name__))
        if strict:
            raise
        return ""


async def _profiles(cid, actor, target, client):
    try:
        members = await Platform().members(cid, limit=2000)
    except ModerationUnavailable:
        raise EmployeeUnavailable("暂时无法核验本群成员，请稍后再试，不会扣币。") from None
    if (actor not in members or target not in members or actor == target
            or members[actor]["protected"] or members[target]["protected"] or is_self(target)):
        raise RuleError("请指定本群另一位已入职成员，不能选择自己或机器人。")
    first = await asyncio.to_thread(client.profile, actor)
    second = await asyncio.to_thread(client.profile, target)
    for person in (first, second):
        if person.get("rank_level") is None:
            person["rank_level"] = rules.rank_level(person.get("rank_name"))
    eligibility(first, second)
    for person in (first, second):
        if person.get("gender") in (None, "", "未知"):
            person["gender"] = await _gender(cid, person["platform_id"])
        if person["gender"] in ("男", "女"):
            person["gender_source"] = "profile"
            continue
        # 资料里没有性别：按名字选一个直接写（屋主 2026-11-06），选不出来才用中性写法。
        guessed = rules.guess_gender(person.get("name") or "")
        if guessed:
            person["gender"] = guessed
            person["gender_source"] = "name"
            person["gender_note"] = "{} 的性别资料没填，这段按名字写作「{}」；不对就请对方在平台补一下性别".format(
                person.get("name") or "对方", guessed)
        else:
            person["gender"] = "未知"
            person["gender_source"] = "unknown"
            person["gender_note"] = "{} 的性别资料没填、名字也看不出来，这段用中性写法".format(
                person.get("name") or "对方")
    return [first, second]


def _gender_notes(profiles):
    """性别资料缺失时的说明（按名字选了一个 / 只能用中性写法），随扣费提醒一起发。"""
    return [person.get("gender_note") for person in profiles if person.get("gender_note")]


def _keep_gender(session, profiles):
    """续写沿用首段的性别快照（方案 §8 的"一场内一致"）：

    只有在首段是"未知"、这次又查到了明确值时，才改用新值（信息变多了可以纠正，
    但绝不会因为中途补资料或改名把已经写稳的人换个性别）。
    """
    try:
        snapshot = {item.get("platform_id"): item.get("gender")
                    for item in json.loads(session.get("profiles") or "[]")}
    except (TypeError, ValueError):
        return
    for person in profiles:
        fixed = snapshot.get(person["platform_id"])
        if fixed in ("男", "女"):
            person["gender"] = fixed
            person.pop("gender_note", None)
            person["gender_source"] = "snapshot"
        elif fixed == "未知" and person.get("gender") in ("男", "女"):
            person.pop("gender_note", None)


def _clean_profiles(profiles):
    """给模型的 participants：去掉只在程序内部用的说明字段。"""
    drop = ("gender_note", "gender_source")
    return [{key: value for key, value in person.items() if key not in drop}
            for person in profiles]


def _billing(profiles, extra):
    """算这次发起的基础成功率、最终概率、费用与分账快照。"""
    actor, target = profiles
    rel = rules.relation(actor["rank_level"], target["rank_level"])
    base = rules.base_probability(actor["rank_level"], target["rank_level"])
    checkout = rules.checkout(extra, base)
    return {
        "relation": rel,
        "base_probability": base,
        "probability": checkout["probability"],
        "extra": extra,
        "fee": checkout["fee"],
        "split_ratio": rules.split_ratio(rel),
    }


def _roll_wins(roll, probability):
    return roll * 100.0 < probability


def _roll():
    """抽一次（0~1）。测试里 monkeypatch 它就能把结果钉死。"""
    return secrets.randbelow(10000) / 10000.0


async def _debit(wallet, store, key, cid, sid, uid, amount, kind, note=""):
    """扣币：先落 pending 再调钱包，成功后标 applied。

    ⚠️ 方案 §11「扣款响应不明」：网络/服务异常时**先按原幂等编号查证**，
    查不到才按"未扣成"处理并抛出 —— 绝不另建一笔扣款重试。
    """
    store.wallet_record(key, cid, sid, kind, uid, amount, "pending", "", note)
    try:
        result = await asyncio.to_thread(wallet.spend, uid, amount, key,
                                         note or "茶水间潜规则消费")
    except WalletUnavailable:
        check = await asyncio.to_thread(wallet.lookup, key)
        if check.get("state") == "applied":
            store.wallet_update(key, "applied", str(check.get("tx") or ""), note)
            return {"ok": True, "tx": check.get("tx")}
        store.wallet_update(key, "unknown", "", "扣款响应不明，待查证")
        raise
    store.wallet_update(key, "applied", str(result.get("tx") or ""), note)
    return result


async def _credit(wallet, store, key, cid, sid, uid, amount, kind, note=""):
    """发币/退款：同样的幂等记账；失败标 owed（待补发），不假装成功。"""
    store.wallet_record(key, cid, sid, kind, uid, amount, "pending", "", note)
    try:
        result = await asyncio.to_thread(wallet.grant, uid, amount, key,
                                         note or "茶水间潜规则分账")
    except (WalletUnavailable, WalletRefused) as exc:
        store.wallet_update(key, "owed", "", str(exc)[:80])
        return {"ok": False, "error": str(exc)}
    store.wallet_update(key, "applied", str(result.get("tx") or ""), note)
    return {"ok": True, "tx": result.get("tx")}


def _win_head(pid, profiles, bill, split):
    actor, target = profiles
    return ("【会话 {}｜{} × {}】本次支付{}币，基础{}币＋加币{}币；成功率{}，判定成功。{}获得{}币。").format(
        pid, actor["name"], target["name"], bill["fee"], rules.BASE_FEE, bill["extra"],
        rules.format_probability(bill["probability"]), target["name"], split)


def _lose_head(bill):
    return ("本次支付{}币，成功率{}，判定失败；目标未获得分账，钱不退，本场结束。").format(
        bill["fee"], rules.format_probability(bill["probability"]))


def _continue_head(pid, profiles, fee, split, segment, self_pay):
    actor, target = profiles
    if self_pay:
        return ("【会话 {}｜{} × {}】本次续写支付{}币，返还{}币；续写进度{}/{}。").format(
            pid, actor["name"], target["name"], fee, split, segment, rules.MAX_CONTINUES)
    return ("【会话 {}｜{} × {}】本次续写支付{}币，目标获得{}币；续写进度{}/{}。").format(
        pid, actor["name"], target["name"], fee, split, segment, rules.MAX_CONTINUES)


async def _deliver_segment(sender, cid, text, reference, store, sid, operation):
    """投递一段正文；返回平台消息 id 或空串。异常语义与旧版一致。"""
    def guard():
        return enabled(cid) and bool(store.dispatch(sid, operation))

    for attempt in range(3):
        try:
            return await reply(sender, cid, text, reference, guard=guard, strict=True)
        except ReplyContentRejected:
            if store.retry_delivery(sid, operation) and store.fail_generation(sid, operation):
                await reply(sender, cid, "平台拒绝了本段内容，本次未发送；这笔不扣币。", reference)
            return ""
        except ReplyNotSent:
            if not store.retry_delivery(sid, operation):
                return ""
            if attempt == 2:
                store.fail_generation(sid, operation)
                return ""
            await asyncio.sleep(attempt + 1)
        except Exception:
            store.ambiguous(sid, operation)
            return ""
    return ""


async def _charge_notice(sender, cid, reference, *, fee, extra, balance, probability,
                        segment=None, notes=()):
    """扣币成功后的即时提醒（正文还在生成，先让玩家知道钱已经扣了）。

    刻意**不透露判定结果**（结果在抽取时已定，但要让文字先说话）；
    提示本身发不出去也不影响主流程 —— 钱已经扣了，正文照发。
    notes：性别资料缺失时的说明（按名字选了一个 / 只能用中性写法）。
    """
    if fee <= 0:
        return ""
    if segment:
        text = "已扣 {} 币（续写第 {} 段），余额 {}；正在写这一段～".format(
            fee, segment, "?" if balance is None else balance)
    else:
        base, added = rules.BASE_FEE, extra
        text = "已扣 {} 币（基础{}币＋加币{}币），余额 {}；成功率 {}，正在写这一段～".format(
            fee, base, added, "?" if balance is None else balance,
            rules.format_probability(probability))
    extra_notes = [note for note in notes if note]
    if extra_notes:
        text += " 注：" + "；".join(extra_notes) + "。"
    return await reply(sender, cid, text, reference)


async def _refund(wallet, store, sid, cid, actor, fee, *, kind=KV_REFUND):
    """系统失败退款。退款失败时标 owed，由补偿任务重试。"""
    key = "{}:{}".format(sid, kind)
    return await _credit(wallet, store, key, cid, sid, actor, fee, "refund")


async def _refund_notice(sender, cid, reference, wallet, store, sid, actor, fee, *, kind=KV_REFUND):
    """退款并把"退款处理中"如实告诉玩家（不宣称已退款）。"""
    result = await _refund(wallet, store, sid, cid, actor, fee, kind=kind)
    if not result.get("ok"):
        await reply(sender, cid, "退款处理中：{}币 稍后自动重试，未到账前不算退款成功。".format(fee),
                    reference)
    return result


async def _settle_split(wallet, store, sid, cid, target, fee, ratio):
    split = rules.split_amount(fee, ratio)
    if split <= 0:
        return 0, {"ok": True}
    result = await _credit(wallet, store, "{}:{}".format(sid, KV_SPLIT), cid, sid,
                           target, split, "split")
    return split, result


async def _run(sender, cid, uid, action, payload, reference, store, client, *, message=None):
    sid = operation = None
    wallet = wallet_adapter()
    try:
        if action == "潜规则":
            if isinstance(wallet, UnavailableWallet):
                raise RuleError(NO_WALLET)
            target = await quoted_target(cid, message)
            if target is not None:
                extra, rest, problem = rules.take_extra(payload.split())
                if problem:
                    raise RuleError(problem)
                direction = " ".join(rest)
            else:
                target_text, rest_tokens = split_request(payload)
                raw_uid = target_text.lstrip("@")
                if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", raw_uid):
                    target = raw_uid
                else:
                    target = await asyncio.to_thread(client.match, target_text)
                extra, rest, problem = rules.take_extra(rest_tokens)
                if problem:
                    raise RuleError(problem)
                direction = " ".join(rest)
            profiles = await _profiles(cid, uid, target, client)
            bill = _billing(profiles, extra)
            if not enabled(cid):
                return
            balance = await asyncio.to_thread(wallet.balance, uid)
            if not balance.get("ok"):
                raise RuleError(str(balance.get("error") or "查不到余额，稍后再试，不会扣币。"))
            if int(balance.get("balance") or 0) < bill["fee"]:
                raise RuleError("余额不足：这次要 {} 币，你只有 {} 币。".format(
                    bill["fee"], int(balance.get("balance") or 0)))
            roll = _roll()
            bill["roll"] = roll
            sid, operation = store.start(cid, uid, target, reference, profiles, **bill)
            try:
                charged = await _debit(wallet, store, "{}:{}".format(sid, KV_FEE), cid, sid, uid,
                                       bill["fee"], "fee", "首段费用")
            except WalletRefused as exc:
                store.end(cid, uid, "failed", expected_sid=sid)
                raise RuleError(str(exc)) from None
            # 先告诉玩家钱已经扣了，再开始生成（生成要几十秒，不能让人干等）
            await _charge_notice(sender, cid, reference, fee=bill["fee"], extra=bill["extra"],
                                 balance=(charged or {}).get("balance"),
                                 probability=bill["probability"], notes=_gender_notes(profiles))
            won = _roll_wins(roll, bill["probability"])
            store.set_outcome(sid, operation, "win" if won else "lose")
            history = json.loads(store.get(sid)["history"])
            people = _clean_profiles(profiles)
            if won:
                text = await asyncio.wait_for(asyncio.to_thread(
                    generate, people, history, direction), timeout=50)
                outcome = "win"
            else:
                text = await asyncio.wait_for(asyncio.to_thread(
                    generate_failure, people, direction), timeout=30)
                outcome = "lose"
            if not enabled(cid):
                store.end(cid, uid, "disabled", expected_sid=sid)
                await _refund_notice(sender, cid, reference, wallet, store, sid, uid, bill["fee"])
                return
            if not store.ready(sid, operation, text):
                await _refund_notice(sender, cid, reference, wallet, store, sid, uid, bill["fee"])
                return
            split = rules.split_amount(bill["fee"], bill["split_ratio"]) if outcome == "win" else 0
            head = (_win_head(sid[:8], profiles, bill, split) if outcome == "win"
                    else _lose_head(bill))
            mid = await _deliver_segment(sender, cid, head + text, reference, store, sid, operation)
            if not mid:
                state = (store.get(sid) or {}).get("status")
                if state == "ambiguous":
                    # 投递结果不明：不退款、不重发、不重复扣费，等核对（方案 §11）。
                    # 刻意不再追发一条提示 —— 那一条同样可能投递不明。
                    log("office_ambiguous [{}] 会话 {}".format(cid[:8], (sid or "")[:8]))
                    return
                store.fail_generation(sid, operation)
                await _refund_notice(sender, cid, reference, wallet, store, sid, uid, bill["fee"])
                return
            store.delivered(sid, operation, mid, outcome=outcome)
            if outcome == "win" and split > 0:
                got, result = await _settle_split(wallet, store, sid, cid, target,
                                                  bill["fee"], bill["split_ratio"])
                if not result.get("ok"):
                    await reply(sender, cid, "分账待到账（{} 币），稍后自动补发。".format(got),
                                reference)
            return

        # ---- 续写（结束/开关在 handle 里处理，这里只处理续写） ----
        if isinstance(wallet, UnavailableWallet):
            raise RuleError(NO_WALLET)
        row = store.current(cid, uid)
        if not row or row["status"] != "active":
            raise RuleError("当前没有可以继续的会话，可能已经结束或正在处理上一段。")
        # 方案 §12 最后一段：在途旧会话（首段没收费，fee=0）续写也不收费，不追溯。
        legacy = int(row.get("fee") or 0) <= 0
        fee = 0 if legacy else rules.CONTINUE_FEE
        try:
            profiles = await _profiles(cid, row["actor"], row["target"], client)
        except RuleError:
            store.end(cid, uid, "ineligible", expected_sid=row["id"])
            raise
        _keep_gender(row, profiles)          # 本场性别快照：中途补资料不会把人换了
        if not enabled(cid):
            return
        if fee:
            balance = await asyncio.to_thread(wallet.balance, uid)
            if not balance.get("ok"):
                raise RuleError(str(balance.get("error") or "查不到余额，稍后再试，不会扣币。"))
            if int(balance.get("balance") or 0) < fee:
                raise RuleError("余额不足：续写要 {} 币，你只有 {} 币。".format(
                    fee, int(balance.get("balance") or 0)))
        reservation = store.begin_continue(cid, uid, reference, profiles,
                                           expected_sid=row["id"], payer=uid)
        if reservation is None:
            return
        sid, operation = reservation
        current = store.get(sid) or {}
        segment = int(current.get("continues") or 0) + 1
        if fee:
            try:
                charged = await _debit(wallet, store, "{}:{}:{}".format(sid, KV_CONT, segment),
                                       cid, sid, uid, fee, "continue",
                                       "续写第 {} 段".format(segment))
            except WalletRefused as exc:
                store.fail_generation(sid, operation)
                raise RuleError(str(exc)) from None
            await _charge_notice(sender, cid, reference, fee=fee, extra=0,
                                 balance=(charged or {}).get("balance"),
                                 probability=None, segment=segment,
                                 notes=_gender_notes(profiles))
        text = await asyncio.wait_for(asyncio.to_thread(
            generate, _clean_profiles(profiles),
            json.loads(current.get("history") or "[]"), payload), timeout=50)
        if not store.ready(sid, operation, text):
            await _refund_notice(sender, cid, reference, wallet, store, sid, uid, fee, kind=KV_CONT + "refund")
            return
        ratio = float(current.get("split_ratio") or 0)
        target = current.get("target") or ""
        split = rules.split_amount(fee, ratio)
        head = _continue_head(sid[:8], profiles, fee, split, segment, uid == target)
        mid = await _deliver_segment(sender, cid, head + text, reference, store, sid, operation)
        if not mid:
            state = (store.get(sid) or {}).get("status")
            if state == "ambiguous":
                log("office_ambiguous [{}] 会话 {}".format(cid[:8], (sid or "")[:8]))
                return
            store.fail_generation(sid, operation)
            await _refund_notice(sender, cid, reference, wallet, store, sid, uid, fee, kind=KV_CONT + "refund")
            return
        store.delivered(sid, operation, mid, outcome="win")
        if split > 0:
            result = await _credit(wallet, store, "{}:{}".format(sid, KV_SPLIT),
                                   cid, sid, target, split, "split")
            if not result.get("ok"):
                await reply(sender, cid, "分账待到账（{} 币），稍后自动补发。".format(split),
                            reference)
        return

    except asyncio.CancelledError:
        if sid:
            store.fail_generation(sid, operation)
        raise
    except (RuleError, EmployeeUnavailable) as error:
        if sid:
            store.fail_generation(sid, operation)
        await reply(sender, cid, str(error), reference)
    except WalletUnavailable as error:
        if sid:
            store.fail_generation(sid, operation)
        await reply(sender, cid, "摸鱼币接口暂时不可用：{}".format(str(error)[:60]), reference)
    except SceneGenerationError as error:
        log("office_model 失败 [{}] 指令={} reason={} chars={} finish={} attempts={}".format(
            cid[:8], reference["id"][:8], error.reason, error.length, error.finish, error.attempts))
        if sid:
            store.fail_generation(sid, operation)
            current = store.get(sid) or {}
            fee = int(current.get("fee") or 0)
            if fee:
                await _refund(wallet, store, sid, cid, uid, fee,
                              kind=KV_REFUND if not current.get("continues") else KV_CONT + "refund")
        hint = ("当前剧情生成繁忙，请稍后再试；这笔不扣币。"
                if error.reason == "office_model_busy" else
                "本段内容过短、过长或未完整结束，已取消发送；这笔不扣币。")
        await reply(sender, cid, hint, reference)
    except Exception as error:
        log("office_task 失败 [{}] {}".format(cid[:8], type(error).__name__))
        if sid:
            state = (store.get(sid) or {}).get("status")
            if state != "ambiguous" and store.fail_generation(sid, operation):
                current = store.get(sid) or {}
                fee = int(current.get("fee") or 0)
                if fee and not current.get("history"):
                    await _refund_notice(sender, cid, reference, wallet, store, sid, uid, fee)
        await reply(sender, cid, "这次剧情没能完成，这笔不扣币，请稍后再试。", reference)


async def handle(sender, cid, uid, text, message, *, dry=False, store=None, client=None):
    parsed = command(text)
    if parsed is None or not enabled(cid) or dry:
        return
    store = store or Store()
    ref = reference_of(message)
    if (not uid or message.get("sent_by") != uid or message.get("chatroom_id") not in (None, cid)
            or not store.claim_command(cid, ref["id"])):
        return
    action, payload = parsed
    if len(payload) > 1000:
        await reply(sender, cid, "剧情方向最多 1000 字，请精简后再发。", ref)
        return
    if action not in ("潜规则", "潜规则继续") and payload:
        await reply(sender, cid, "这条命令不需要额外参数哦。", ref)
        return
    if action in ("潜规则规则", "潜规则帮助"):
        # 这两条都是多行说明书，保留换行（其余回复仍然压成一行）
        body = rules.help_text() if action == "潜规则帮助" else rules.rules_text()
        await reply(sender, cid, body, ref, keep_lines=True)
    elif action in ("允许潜规则", "拒绝潜规则"):
        allow = action == "允许潜规则"
        store.preference(cid, uid, allow)
        if not allow:
            for task in tuple(TASKS):
                if getattr(task, "office_owner", None) == (cid, uid):
                    task.cancel()
        await reply(sender, cid, "已允许其他人向你发起，可跨部门。" if allow else
                    "已拒绝潜规则；你参与的会话已结束，尚未发送的内容已取消。", ref)
    elif action == "潜规则状态":
        status = store.status(cid, uid)
        detail = "当前{}；发起不限次数（首段 {} 币起，看余额）；{}。".format(
            "允许" if status["allowed"] else "拒绝", rules.BASE_FEE,
            "有进行中的会话" if status["active"] else "没有进行中的会话")
        if status.get("active"):
            detail += "续写进度 {}/{}，本场费用 {} 币".format(
                status.get("continues", 0), rules.MAX_CONTINUES, status.get("fee", 0))
        if status.get("pending_ops"):
            detail += "；待处理账务 {} 笔".format(status["pending_ops"])
        await reply(sender, cid, detail, ref)
    elif action == "潜规则结束":
        ended = store.end(cid, uid)
        for task in tuple(TASKS):
            if getattr(task, "office_owner", None) == (cid, uid):
                task.cancel()
                ended = True
        await reply(sender, cid, "本场结束。" if ended else "你当前没有进行中的会话。", ref)
    else:
        if len(TASKS) >= 4:
            await reply(sender, cid, "小小糯正在处理其他剧情，稍后再试，不会扣币。", ref)
            return
        task = asyncio.create_task(_run(sender, cid, uid, action, payload, ref, store,
                                        client or Client(), message=message))
        task.office_owner = (cid, uid)
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)


async def tick(sender, cid, *, dry=False, store=None, now=None):
    if cid != TEA_CID or dry:
        return
    store = store or Store()
    if not enabled(cid) and not store.path.exists():
        return
    for notice in store.sweep(cid, now=now, enabled=enabled(cid)):
        mid = await reply(sender, cid, notice["text"], json.loads(notice["reference"]))
        store.notice_result(notice["id"], bool(mid))
