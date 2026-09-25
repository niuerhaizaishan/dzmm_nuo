"""runtime.outbound 的单测（纯函数，无外部依赖）。

覆盖：长消息阈值、分片不超限、内容不丢失、发送路由表。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.contracts import DeliveryKind                      # noqa: E402
from runtime.outbound import (                                   # noqa: E402
    BOT_GROUP_MAX_CHARS,
    BOT_GROUP_MAX_NEWLINES,
    group_message_chunks,
    requires_bot_group_sender,
    should_use_bot,
)


# ---------------------------------------------------------------- 阈值

def test_short_text_does_not_require_bot():
    assert requires_bot_group_sender("你好呀～") is False


def test_threshold_is_exclusive_on_both_limits():
    assert requires_bot_group_sender("A" * BOT_GROUP_MAX_CHARS) is False
    assert requires_bot_group_sender("A" * (BOT_GROUP_MAX_CHARS + 1)) is True

    assert requires_bot_group_sender("\n" * BOT_GROUP_MAX_NEWLINES) is False
    assert requires_bot_group_sender("\n" * (BOT_GROUP_MAX_NEWLINES + 1)) is True


# ---------------------------------------------------------------- 分片

def _assert_chunks_within_limits(chunks):
    for i, c in enumerate(chunks):
        assert len(c) <= BOT_GROUP_MAX_CHARS, f"第{i}片超长: {len(c)}"
        assert c.count("\n") <= BOT_GROUP_MAX_NEWLINES, f"第{i}片换行过多"


def test_single_line_overlong_is_chunked_within_limits():
    chunks = group_message_chunks("A" * 2500)
    _assert_chunks_within_limits(chunks)
    assert "".join(chunks) == "A" * 2500


def test_many_lines_are_chunked_within_limits():
    text = "\n".join(f"第{i}行内容" for i in range(60))
    chunks = group_message_chunks(text)
    _assert_chunks_within_limits(chunks)
    # 换行可能在切分边界被吞，但非换行字符必须一字不丢
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_realistic_long_novel_like_text():
    """公演小说那种"长 + 多行"混合内容。"""
    para = "（她抬手，指尖擦过杯沿）" + "这是一段用来占位的中文叙述。" * 12
    text = "\n".join(para for _ in range(12))
    chunks = group_message_chunks(text)
    assert len(chunks) > 1
    _assert_chunks_within_limits(chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_short_text_is_a_single_chunk():
    assert group_message_chunks("你好") == ["你好"]


# ---------------------------------------------------------------- 路由表

def test_route_group_text_to_bot_when_enabled():
    assert should_use_bot(bot_enabled=True) is True


def test_no_bot_when_disabled():
    assert should_use_bot(bot_enabled=False) is False


def test_private_chat_never_uses_bot():
    assert should_use_bot(delivery_kind=DeliveryKind.DIRECT, bot_enabled=True) is False


def test_non_text_content_never_uses_bot():
    for ct in ("image", "share"):
        assert should_use_bot(content_type=ct, bot_enabled=True) is False


def test_recall_required_never_uses_bot():
    assert should_use_bot(recall_after_seconds=30, bot_enabled=True) is False


def test_long_group_text_still_uses_bot():
    """超长不是"不用 Bot"的理由——恰恰相反，超长只能靠 Bot。"""
    assert should_use_bot(bot_enabled=True) is True


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL {name}: {e}")
    print("失败" if failed else "全部通过")
    raise SystemExit(1 if failed else 0)
