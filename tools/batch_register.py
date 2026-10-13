# -*- coding: utf-8 -*-
"""批量注册：把 rose 两群（13a7287a / ade37d32）全员建档（2026-10-13 屋主指令）。

复用 /注册 的建档结构（register_user 的字段集，dispatch.py）：
  · 已注册幂等跳过；config bots（小小糯/薇丝/若瑾）跳过；
  · get_user 拿性别/签名/简介 —— 节流 0.75s（平台限流 90/min，含 buffer）；
  · 最近 1000 条窗口内有发言 → 完整注册（generate_profile_summary + XP，每人 2 次 LLM）；
  · 无发言（潜水者）→ 轻档案：register_summary/xp 留空 —— 无发言硬调 LLM 只会编造，
    以后她说话了 /查 时的 _refresh_profile_latest 和档案扫描自然会丰富；
  · upsert_profile 逐人落盘 —— 断点安全，中断重跑自动跳过已注册。

用法（容器内）：
    python /tmp/batch_register.py > /tmp/batch_register.log 2>&1
进度：tail /tmp/batch_register.log
"""
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, '/app')


def out(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def main():
    c = json.load(open('/app/config/config.json', encoding='utf-8'))
    TARGETS = ('13a7287a', 'ade37d32')
    groups = {str(g['cid'])[:8]: g['cid'] for g in c['groups']}
    bot_uids = {str(b['uid']) for b in (c.get('bots') or []) if b.get('uid')}

    from runtime import ctx
    from runtime.identity import get_full_name
    from runtime.members import _extract_msgs, _member_profile_text
    from runtime.dispatch import _extract_xp
    from profiles import get_profile, upsert_profile
    from dzmm import get_messages_all, get_members, get_user
    from brains import generate_profile_summary

    API_GAP = 0.75          # get_user 节流（平台限流 90/min）
    stats = {'registered': 0, 'skipped': 0, 'bots': 0, 'llm': 0, 'fail': 0}

    for pfx in TARGETS:
        cid = groups[pfx]
        ctx.set_active_cid(cid)
        try:
            out('== 群', pfx)
            members = get_members(cid)
            out('成员', len(members))
            msgs = get_messages_all(cid, max_count=1000)
            out('窗口消息', len(msgs))
            for uid, name in members.items():
                try:
                    if not name or uid in bot_uids:
                        stats['bots'] += 1
                        continue
                    if get_profile(uid):
                        stats['skipped'] += 1
                        continue
                    full = get_full_name(uid) or name
                    u = get_user(cid, uid) or {}
                    time.sleep(API_GAP)
                    their = _extract_msgs(uid, msgs)
                    summary, xp = '', []
                    if their:
                        try:
                            profile_text = _member_profile_text(uid, full, msgs)
                            summary = (generate_profile_summary(profile_text) or '').strip()
                            xp = _extract_xp(profile_text)
                            stats['llm'] += 1
                        except Exception as le:
                            out('  LLM失败', full[:16], type(le).__name__)
                            summary, xp = '', []
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    profile = {
                        'name': full,
                        'gender': str(u.get('gender')) if u and u.get('gender') else '',
                        'signature': (u.get('quirk') or '') if u else '',
                        'bio': (u.get('bio') or '') if u else '',
                        'registered_at': now,
                        'register_summary': summary,
                        'xp': xp,
                        'last_seen': now,
                        'query_count': 0,
                    }
                    if upsert_profile(uid, profile):
                        stats['registered'] += 1
                        if stats['registered'] % 20 == 0:
                            out('进度: 已注册', stats['registered'])
                    else:
                        stats['fail'] += 1
                        out('  落盘失败', full[:16])
                except Exception as pe:
                    stats['fail'] += 1
                    out('  单人异常', str(uid)[:8], type(pe).__name__, str(pe)[:60])
        finally:
            ctx.reset_active_cid()
    out('完成', json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
