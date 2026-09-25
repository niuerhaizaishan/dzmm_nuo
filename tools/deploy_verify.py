"""已部署产物的验收检查（在目标镜像**内部**运行）。

只读：不启动服务、不联网、不发任何群消息。
用途：宿主机内存不足以再跑一个常驻容器时，仍能证明「代码确实能在这个环境里跑起来」。

用法（在服务器上）：
    docker run --rm -v /opt/dzmm-botapi:/app:ro -v /tmp/deploy_verify.py:/verify.py:ro \
        -w /app dzmm-bot:latest python /verify.py
"""
import os
import sys
from pathlib import Path

print("cwd    :", os.getcwd())
print("python :", sys.version.split()[0])
print()

fails = []


def check(name, ok, detail=""):
    print("  [{}] {}{}".format("OK" if ok else "!!", name,
                               (" -- " + str(detail)) if detail else ""))
    if not ok:
        fails.append(name)


# 1) runtime 子包是否真的传上去了（P4.0 修的就是这个）
try:
    import runtime
    from runtime.outbox import OutboxSender, OutboxStore
    from runtime.routing import RoutingSender
    from runtime.transport_bot import DzmmBotSender
    check("runtime 包可导入", True, Path(runtime.__file__).parent)
except Exception as e:                                     # noqa: BLE001
    check("runtime 包可导入", False, e)

# 2) bot 能否导入，并自报 runtime 可用
try:
    import bot
    check("bot 可导入", True)
    check("_RUNTIME_IMPORT_OK", bot._RUNTIME_IMPORT_OK,
          getattr(bot, "_RUNTIME_IMPORT_ERR", ""))
    check("is_self / self_uids / self_names 齐全",
          all(callable(getattr(bot, n, None))
              for n in ("is_self", "self_uids", "self_names")))
    check("self_uids 默认只含侧车账号", bot.self_uids() == {bot.NUO_UID}, bot.self_uids())
    check("NUONUO_UID 未被迁移改坏",
          bot.NUONUO_UID == "a56cbd67-3a1e-439a-8beb-d1557dbeff79")
except Exception as e:                                     # noqa: BLE001
    check("bot 可导入", False, e)
    print()
    print("FAILED: {}".format(fails))
    raise SystemExit(1)

# 3) 出站队列在容器里真的能落库
try:
    store = OutboxStore(os.environ.get("DZMM_OUTBOX_DB", "/tmp/outbox_verify.db"))
    oid = store.enqueue(chatroom_id="verify-cid", text="verify")
    check("outbox 落库为 pending", store.get(oid)["status"] == "pending")
    check("outbox 可领租约", store.lease(oid, "verify-worker") is True)
    store.mark_sent(oid, "verify-message-id")
    row = store.get(oid)
    check("outbox 可标记已发", row["status"] == "sent" and row["platform_sent_id"] == "verify-message-id")
    store.close()
except Exception as e:                                     # noqa: BLE001
    check("outbox 可用", False, e)


# 4) 路由与 bot 级链路装配
class FakeAccount:
    async def send(self, text, cid, allow_fallback=True):
        return True


try:
    r = RoutingSender(FakeAccount(), {"verify-cid": DzmmBotSender("https://example.invalid", "tok")})
    check("路由按 cid 装配", r.bot_cids == ("verify-cid",) and r.bot_for("verify-cid") is not None)
except Exception as e:                                     # noqa: BLE001
    check("路由按 cid 装配", False, e)

try:
    os.environ.pop("DZMM_BOT_ROUTING", None)
    os.environ["DZMM_OUTBOX_DB"] = "/tmp/outbox_verify2.db"
    w = bot._wrap_with_outbox(FakeAccount(), dry=False)
    check("默认不启用 Bot 路由（接线但不上线）",
          type(w).__name__ == "OutboxSender" and type(w._inner).__name__ == "FakeAccount",
          "链路: {} -> {}".format(type(w).__name__, type(w._inner).__name__))
except Exception as e:                                     # noqa: BLE001
    check("默认不启用 Bot 路由", False, e)

print()
print("FAILED: {}".format(fails) if fails else "ALL CHECKS PASSED")
raise SystemExit(1 if fails else 0)
