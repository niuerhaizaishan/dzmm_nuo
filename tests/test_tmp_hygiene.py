"""P12-AB：测试临时目录不许再无限泄漏（体检时 `tests/_tmp` 攒了 2,202 个目录）。

根因：`dbdir` 的清理是 `shutil.rmtree(..., ignore_errors=True)`，而很多测试直接
`OutboxStore(dbdir / "o.db")` **不关连接** —— Windows 上句柄还开着就删不掉，
错误又被静默忽略，于是没人发现。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest                                                # noqa: E402


def test_force_rmtree_removes_a_dir_holding_a_sqlite_file(dbdir):
    """目录里有个（已释放的）sqlite 文件也要能删掉。"""
    inner = dbdir / "inner"
    inner.mkdir()
    con = sqlite3.connect(str(inner / "x.db"))
    con.execute("create table t(a)")
    con.commit()
    con.close()                       # 显式关掉（模拟 gc 释放后的状态）
    assert conftest._force_rmtree(inner) is True
    assert not inner.exists()


def test_dbdir_fixture_points_inside_the_repo(dbdir):
    """临时目录必须落在仓库内（沙箱下系统 Temp 写不进去）。"""
    assert str(dbdir).startswith(str(conftest.TMP_ROOT))
    assert dbdir.exists()


def test_cleanup_is_not_silent():
    """清理函数必须**把结果返回出来**（旧写法 ignore_errors=True 等于瞒报）。"""
    import inspect
    src = inspect.getsource(conftest._force_rmtree)
    assert "return not d.exists()" in src, "要如实返回有没有删掉"
    assert "onerror=_on_rmtree_error" in src, "删不掉要重试，而不是忽略"
    # 去掉模块 docstring 再扫（文档里提到旧写法是正常的，别误判）
    mod_src = inspect.getsource(conftest)
    body = mod_src.split('"""', 2)[2] if mod_src.count('"""') >= 2 else mod_src
    assert "ignore_errors=True" not in body, "代码里又出现静默忽略"
