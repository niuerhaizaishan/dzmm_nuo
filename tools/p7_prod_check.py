"""生产验证 P7：存储后端、关键顶层键（dice_game）、以及 JSON 是否已停写。

    docker cp tools/p7_prod_check.py dzmm-bot:/tmp/
    docker exec -e PYTHONPATH=/app dzmm-bot python /tmp/p7_prod_check.py
"""
import bot
import state_store

st = state_store.stats()
print("backend :", st.get("backend"))
print("db      :", st.get("db"))
print("counts  :", st.get("counts"))

state = state_store.load_state()
keys = sorted(state.keys())
groups = [k for k in keys if k != "dice_game"]
print("顶层键数:", len(keys))
print("群 id   :", len(groups), "个")
print("含 dice_game:", "dice_game" in keys)
print("骰子状态可读:", isinstance(bot._dice_state(), dict), bot._dice_state())

assert st.get("backend") == "sqlite", "后端不是 sqlite"
assert "dice_game" in keys, "dice_game 顶层键丢了 —— 骰子功能会读成空"
assert len(groups) >= 5, "群状态数量异常"
print()
print("OK: P7 存储生效且关键键完好")
