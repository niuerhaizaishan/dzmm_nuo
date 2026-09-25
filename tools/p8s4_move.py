"""P8 第 4 步的迁移机制：把 bot.py 的模块级函数**整段搬**到新模块。

沿用 P4.2 / P8.3 的纪律：**程序化搬运 + 逐项比对 + 任何不符即中止**。

为什么不能靠手工剪切粘贴：
  搬走的函数如果在 bot.py 里偷偷引用了某个模块级全局（比如 `_ACTIVE_CID`），
  搬出去之后 **import 不会报错**，只在真正调用时 `NameError`。
  这意味着一半的错要等到线上有人发那条指令才炸 —— P8 第 3 步那种"抄错一个字"
  的风险，在这里会升级成"运行时才炸"。所以本工具在写盘**之前**做静态依赖检查。

三道闸门（任一不过就不写盘）：
  1. 源文件里每个函数名/常量名存在且**唯一**，要删的文本也唯一
  2. 组装出的新模块里，被搬函数引用的每个模块级名字都必须有来源
     （兄弟函数 / 搬过来的常量 / 头部 import / 内置）——否则中止
  3. 搬完重新 import bot，逐个用 `inspect.getsource` 比对源码文本与搬之前一致

用法：
    python tools/p8s4_move.py <slice> --dry    # 只看会发生什么
    python tools/p8s4_move.py <slice>          # 执行
"""
import ast
import builtins
import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot.py"
BOT_IMPORT_ANCHOR = "import state_store"      # bot.py 里新 import 插在它后面


class Slice:
    def __init__(self, module, header, funcs=(), consts=(), remove_texts=(),
                 rewrites=(), export_in_bot=True):
        self.module = module
        self.header = header
        self.funcs = list(funcs)
        self.consts = list(consts)
        self.remove_texts = list(remove_texts)
        # 搬运时必须一起改的文本（例如 `_ACTIVE_CID` -> `ctx.get_active_cid()`）。
        # 声明式：工具会逐条打印出来，而且"搬完比对"以**改写后**的文本为基准，
        # 所以它仍然能证明"除了这些声明过的改写，一个字符都没动"。
        self.rewrites = list(rewrites)
        self.export_in_bot = export_in_bot


SLICES = {}

# ---------------------------------------------------------------- 第 0 片：地基
SLICES["obs"] = Slice(
    module="runtime/obs.py",
    header='''"""日志（P8 第 4 步从 bot.py 拆出的叶子工具）。

`log` 被 bot.py 里几乎每个函数调用，是最典型的"叶子工具"：只依赖 `ROOT`
和 `datetime`，谁都不依赖。**必须先把它搬出来** —— 否则后面每拆一个模块，
新模块都要为了打一行日志回指 bot.py。
"""
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

__all__ = ["log", "ROOT"]
''',
    funcs=["log"],
)

SLICES["identity"] = Slice(
    module="runtime/identity.py",
    header='''"""双身份 + 昵称解析（P8 第 4 步从 bot.py 拆出的叶子工具）。

P4 起「我自己」有**两个**身份，全项目必须一致地认这两个：
  NUO_UID —— 侧车账号（登录态持有者）：私聊 / 图片 / 分享 / 历史数据里的旧发言
  BOT_UID —— 官方 Bot：群文本发送（P4.1 的 RoutingSender）

回声过滤与「排除自己」必须同时认这两个账号，否则 Bot 的发言会被当成群友 ——
轻则统计污染，重则自触发循环。

昵称解析（`get_full_name`/`get_name`）也放这里：判定「@的是不是我」需要它，
而它又是几乎所有模块都要用的东西。原始昵称来自成员表，查不到时兜底读档案。

`_name_cache` 是**可变对象**：bot.py 用 `from ... import _name_cache` 拿到同一个
dict 就地读写，所以留在 bot.py 的两个使用点（`_daily_profile_scan`、
`_find_uid_by_name`）不需要改任何代码。
"""
import os

from dzmm import get_members, get_user
from profiles import get_profile
from runtime import ctx

# ---------------------------------------------------------------- 身份
NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"  # 侧车账号（登录态持有者）
BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()
BOT_NAME = (os.environ.get("DZMM_BOT_NAME") or "小小糯").strip()
BOT_NAME_SHORT = (os.environ.get("DZMM_BOT_SHORT") or "小小糯").strip()

# 昵称缓存（uid -> 昵称），跨轮次复用
_name_cache: dict = {}

__all__ = [
    "NUO_UID", "BOT_UID", "BOT_NAME", "BOT_NAME_SHORT",
    "self_uids", "is_self", "self_names",
    "_name_cache", "_load_member_names", "get_full_name", "get_name", "is_at_me",
]
''',
    funcs=["self_uids", "is_self", "self_names",
           "_load_member_names", "get_full_name", "get_name", "is_at_me"],
    # 常量**不搬**，由头部定义（头部版本带注释、是权威定义处）；bot.py 里的原定义
    # 用 remove_texts 删掉。搬常量会与头部重复定义 —— 第一版就是这么写的，
    # 结果同一组常量在文件里出现两次。
    consts=[],
    rewrites=[("_ACTIVE_CID", "ctx.get_active_cid()")],
    remove_texts=[
        "# ---------------------------------------------------------------- 自身身份（P4.2）\n"
        "# 从 P4 起，「我自己」有**两个**身份，必须分清：\n"
        "#   NUO_UID  —— 侧车账号：私聊 / 图片 / 分享 / 其他群，以及历史数据里的旧发言\n"
        "#   BOT_UID  —— 官方 Bot：群文本发送（P4.1 的 RoutingSender）\n"
        "# 回声过滤与「排除自己」必须同时认这两个。否则 Bot 的发言会被当成群友 ——\n"
        "# 轻则统计污染，重则自触发循环。\n",
        "# 昵称缓存（uid -> 昵称），跨轮次复用\n",
        'NUO_UID = "503e3cb9-ee6f-4e17-be51-e4ca399b4515"  # 侧车账号（登录态持有者）\n',
        'BOT_UID = (os.environ.get("DZMM_BOT_ID") or "").strip()\n',
        'BOT_NAME = (os.environ.get("DZMM_BOT_NAME") or "小小糯").strip()\n',
        'BOT_NAME_SHORT = (os.environ.get("DZMM_BOT_SHORT") or "小小糯").strip()\n',
        "_name_cache = {}\n",
    ],
)

# ---------------------------------------------------------------- 第 1 片：骰子局
SLICES["dice"] = Slice(
    module="runtime/dice.py",
    header='''"""吹牛骰子局的状态机（P8 第 4 步从 bot.py 整段搬出）。

为什么第一个拆它：侦察量出来的耦合最低 ——
  fan_in = 1（只有 main_loop 调它）
  fan_out = 2（只要 `get_name` / `log`）
  引用的模块级全局 **0 个**
拆完对 bot.py **零回指**，所以它同时也是"拆分机制"本身的验证。

纯牌面规则本来就在 `dice_game.py`（发牌 / 叫点 / 判定 / 开牌）；
这里只管**局面**：座位、轮次、看板、各方指令、私聊发骰。
"""
import asyncio
import time

from dice_game import count_point, dice_str, is_legal_raise, roll_dice, roll_wild
from dzmm import get_private_cid
from runtime.identity import get_name
from runtime.obs import log
import state_store

__all__ = [
    "_dice_state", "_dice_save", "_dice_current_player", "_dice_send_private",
    "_dice_board_text", "_dice_deal_round", "_dice_start_cmd", "_dice_join_cmd",
    "_dice_exit_cmd", "_dice_begin_cmd", "_dice_next_round_cmd", "_dice_call_cmd",
    "_dice_open_cmd", "_dice_quit_cmd", "_dice_skip_cmd", "_dice_status_text",
]
''',
    funcs=["_dice_state", "_dice_save", "_dice_current_player",
           "_dice_send_private", "_dice_board_text", "_dice_deal_round",
           "_dice_start_cmd", "_dice_join_cmd", "_dice_exit_cmd",
           "_dice_begin_cmd", "_dice_next_round_cmd", "_dice_call_cmd",
           "_dice_open_cmd", "_dice_quit_cmd", "_dice_skip_cmd",
           "_dice_status_text"],
    remove_texts=["# ============ 吹牛骰子游戏 ============"],
)


# ---------------------------------------------------------------- 工具
def read_raw(p: Path) -> str:
    return p.read_bytes().decode("utf-8")


def to_lf(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def norm_src(s: str) -> str:
    return to_lf(s).strip()


def span(node) -> tuple:
    """节点的**完整**行范围（含装饰器）。"""
    start = node.lineno
    for d in getattr(node, "decorator_list", []) or []:
        start = min(start, d.lineno)
    return start, node.end_lineno


def block_of(lines, node) -> str:
    s, e = span(node)
    return "".join(lines[s - 1:e])


def bound_names(module_src: str) -> set:
    tree = ast.parse(module_src)
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
    return out


def needed_names(func_src: str) -> set:
    node = ast.parse(func_src).body[0]
    local = set()
    for a in (node.args.args + node.args.kwonlyargs + node.args.posonlyargs):
        local.add(a.arg)
    if node.args.vararg:
        local.add(node.args.vararg.arg)
    if node.args.kwarg:
        local.add(node.args.kwarg.arg)
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            local.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)) and n is not node:
            local.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            local.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                local.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.comprehension) and isinstance(n.target, ast.Name):
            local.add(n.target.id)
    return {n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id not in local}


def apply_rewrites(src: str, rewrites) -> str:
    for old, new in rewrites:
        src = src.replace(old, new)
    return src


def main(argv) -> int:
    if len(argv) < 2 or argv[1] not in SLICES:
        print("用法: python tools/p8s4_move.py {{{}}} [--dry]".format(
            "|".join(sorted(SLICES))))
        return 2
    name, dry = argv[1], "--dry" in argv
    sl = SLICES[name]

    raw = read_raw(BOT)
    lines = raw.splitlines(keepends=True)
    tree = ast.parse(raw)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            consts[n.targets[0].id] = n
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            consts[n.target.id] = n

    # ---------- 闸门 0：存在性 / 防重复执行 ----------
    target = ROOT / sl.module
    if target.exists() and "P8 第 4 步" in read_raw(target):
        print("[中止] {} 已存在，看起来已经搬过了".format(sl.module))
        return 1
    for label, want, pool in (("函数", sl.funcs, funcs), ("常量", sl.consts, consts)):
        miss = [x for x in want if x not in pool]
        if miss:
            print("[中止] bot.py 里找不到这些{}: {}".format(label, miss))
            return 1

    # ---------- 组装新模块 ----------
    blocks = {f: norm_src(apply_rewrites(block_of(lines, funcs[f]), sl.rewrites))
              for f in sl.funcs}
    cblocks = {c: norm_src(apply_rewrites(block_of(lines, consts[c]), sl.rewrites))
               for c in sl.consts}
    parts = [to_lf(sl.header).rstrip("\n")]
    for c in sl.consts:
        parts.append(cblocks[c])
    for f in sl.funcs:
        parts.append(blocks[f])
    module_src = "\n\n\n".join(parts) + "\n"

    # ---------- 闸门 1：新模块里的未解析依赖 ----------
    have = bound_names(module_src) | set(dir(builtins))
    unresolved = {}
    for f in sl.funcs:
        bad = sorted(needed_names(blocks[f]) - have)
        if bad:
            unresolved[f] = bad
    print("目标模块      : {}（{} 行，{} 函数，{} 常量）".format(
        sl.module, len(module_src.splitlines()), len(sl.funcs), len(sl.consts)))
    if sl.rewrites:
        for old, new in sl.rewrites:
            hit = [f for f in sl.funcs if old in block_of(lines, funcs[f])]
            print("声明式改写    : {!r} -> {!r}（命中 {}）".format(
                old, new, ", ".join(hit) or "无"))
    print("未解析依赖    : {}".format("无" if not unresolved else unresolved))
    if unresolved:
        print("[中止] 搬出去会 NameError（只在调用时才炸）。先补 import，或先搬它的依赖。")
        return 1

    # ---------- 计算删除集 ----------
    drop = set()
    for f in sl.funcs:
        s, e = span(funcs[f])
        drop.update(range(s - 1, e))
    for c in sl.consts:
        s, e = span(consts[c])
        drop.update(range(s - 1, e))
    hay = to_lf(raw)
    for text in sl.remove_texts:
        t = to_lf(text).strip("\n")
        n = hay.count(t)
        if n != 1:
            print("[中止] 待删文本出现 {} 次（要求 1）: {!r}".format(n, t[:60]))
            return 1
        start_line = hay.count("\n", 0, hay.index(t))
        drop.update(range(start_line, start_line + t.count("\n") + 1))

    kept = [(i, ln) for i, ln in enumerate(lines) if i not in drop]
    boundary = {p for p, (i, _) in enumerate(kept) if (i - 1) in drop or (i + 1) in drop}

    # 只把**被删出来的** >=3 连续空行收成 2 行（不碰原文里的空行）
    out = [ln for _, ln in kept]
    run = []
    for pos in range(len(out) - 1, -1, -1):
        if out[pos].strip() == "":
            run.append(pos)
            continue
        if len(run) >= 3 and any(r in boundary for r in run):
            for r in run[:-2]:
                out[r] = None
        run = []
    if len(run) >= 3 and any(r in boundary for r in run):
        for r in run[:-2]:
            out[r] = None
    bot_src = "".join(ln for ln in out if ln is not None)

    # ---------- bot.py 补 import（re-export：45 个调用点一处不改）----------
    if sl.export_in_bot:
        modname = sl.module[:-3].replace("/", ".") if sl.module.endswith(".py") \
            else sl.module.replace("/", ".")
        blines = bot_src.splitlines(keepends=True)
        # 锚点用"最后一个顶层 import 行"，不用名字匹配：
        # `import state_store` 在 bot.py 里出现两次（模块级 + _log_runtime_status 里），
        # 按名字匹配曾把 import 插进了那个函数体，直接语法错误。
        hits = [i for i, ln in enumerate(blines)
                if ln[:1] not in (" ", "\t")
                and (ln.startswith("import ") or ln.startswith("from "))]
        if not hits:
            print("[中止] bot.py 里找不到任何顶层 import 行")
            return 1
        eol = "\r\n" if blines[0].endswith("\r\n") else "\n"
        blines.insert(hits[-1] + 1, "from {} import {}  # P8 第 4 步：搬到 {}{}".format(
            modname, ", ".join(sl.funcs + sl.consts), sl.module, eol))
        bot_src = "".join(blines)

    # ---------- 闸门 1.5：改完的 bot.py 必须还能解析 ----------
    # 便宜且高价值：import 插错位置这类错误在这里就挡住，不会写进文件。
    try:
        ast.parse(bot_src)
    except SyntaxError as e:
        print("[中止] 改写后的 bot.py 语法错误（第 {} 行: {}）".format(e.lineno, e.msg))
        print("       出问题的行: {!r}".format(
            bot_src.splitlines()[e.lineno - 1].strip()[:80] if e.lineno else ""))
        return 1

    print("bot.py        : {} 行 -> {} 行（-{}）".format(
        len(lines), len(bot_src.splitlines()),
        len(lines) - len(bot_src.splitlines())))
    if dry:
        print("\n--- 新模块前 34 行 ---")
        print("\n".join(module_src.splitlines()[:34]))
        return 0

    # ---------- 写盘 ----------
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(module_src.encode("utf-8"))
    BOT.write_bytes(bot_src.encode("utf-8"))
    print("已写入        : {}".format(sl.module))

    # ---------- 闸门 2：搬完逐个比对源码文本 ----------
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import bot
    importlib.reload(bot)
    mod = importlib.import_module(sl.module[:-3].replace("/", "."))

    bad = []
    for f in sl.funcs:
        want = blocks[f]
        obj = getattr(bot, f, None)
        if obj is None:
            bad.append((f, "bot 上没有这个名字（re-export 失败）"))
        elif getattr(mod, f, None) is not obj:
            bad.append((f, "bot 和 {} 上不是同一个对象".format(sl.module)))
        elif norm_src(inspect.getsource(obj)) != want:
            bad.append((f, "源码文本与搬之前不一致"))
    print()
    print("比对 {} 个函数：{}".format(
        len(sl.funcs), "全部一致" if not bad else "**不一致**"))
    for f, why in bad[:8]:
        print("    {} -> {}".format(f, why))
    if bad:
        print("[失败] 用 git checkout bot.py 重来")
        return 1

    refs = [ln.strip() for ln in module_src.splitlines()
            if ln.strip().startswith(("import bot", "from bot import"))]
    print("回指 bot.py   : {} 处{}".format(
        len(refs), "" if not refs else " -> " + str(refs)))
    print()
    print("OK 搬完")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
