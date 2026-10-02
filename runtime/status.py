"""启动时运行时状态自证（P3-10 从 bot.py 拆出）。

把 P5/P7/P8/P11/P12 各层"到底生效没有、用的是文件还是代码里的默认值"
在启动日志里一行行打出来 —— 这些能力默认不输出任何东西，没这几行，
"文件没挂上 → 悄悄退回默认值"就只能等出事才发现。

**纯只读**：本模块不持有任何状态，群集合 / 配置对象 / 各注册表全部由 bot 侧
（`bot._log_runtime_status` 注入壳）kw-only 传入，monkeypatch `bot.*` 的
既有接缝不受影响。函数体里 session_guard / throttle_guard / state_store /
runtime.prompts 仍是**函数内 import** —— 缺了哪层，try/except 才能兜住并如实打印。
"""
from runtime.obs import log


__all__ = ["log_runtime_status"]


def log_runtime_status(*, cfg, groups, mute_cids, screen_cids, push_cids,
                       features, personas, tuning,
                       bot_uids, config_personas) -> None:
    """启动时打印 P5 / P7 的运行时状态。

    没有这几行，这些能力生效与否在日志里看不出来（它们默认不输出任何东西）。
    """
    try:
        from session_guard import version as _sver
        from throttle_guard import stats as _tstats
        s = _tstats() or {}
        b = s.get("bucket") or {}
        log("P5: 会话锁=on(version={}) 限流={} rate={}/min burst={}".format(
            _sver(), "on" if s.get("enabled") else "off",
            b.get("rate_per_min"), b.get("capacity")))
    except Exception as e:                                 # noqa: BLE001
        log("P5: 状态未知（" + str(e)[:60] + "）")
    try:
        import state_store
        st = state_store.stats()
        log("P7: 存储={} db={} counts={}".format(
            st.get("backend"), st.get("db") or "-", st.get("counts") or {}))
    except Exception as e:                                 # noqa: BLE001
        log("P7: 状态未知（" + str(e)[:60] + "）")
    try:
        import runtime.prompts as _prompts
        ps = _prompts.stats()
        # 这行是 P8 第 3 步的自证：prompt 到底来自文件还是代码里的默认值。
        # 外置最常见的坑是"文件没挂上 → 悄悄退回默认值"，不打印就看不出来。
        log("P8: prompt dir={} 来自文件={}/{} 磁盘文件={}".format(
            ps.get("dir"), ps.get("from_file"), ps.get("total"),
            ps.get("files_on_disk")))
    except Exception as e:                                 # noqa: BLE001
        log("P8: 无 runtime.prompts，prompt 全部用代码默认值（"
            + str(e)[:50] + "）")
    try:
        # P11 A 期自证：群清单到底来自 config.json 还是代码里的默认值。
        # 与 P8 同一个道理 —— "悄悄退回默认值"必须能一眼看出来。
        log("P11: 配置={} 群={} 静默={} 筛查={} 推送={} Bot={} path={}".format(
            cfg.source, len(groups), len(mute_cids), len(screen_cids),
            len(push_cids), len(cfg.bots), cfg.path))
        for b in cfg.bots:
            log("P11: Bot key={} name={} cid={} token={} uid={}".format(
                b.key, b.name, (b.cid or "")[:8],
                "已设置" if b.resolve_token() else "**未设置**",
                (b.uid or "未配置")[:8]))
        log("P11: 我是谁={}".format(
            ",".join(sorted(u[:8] for u in bot_uids())) or "（只有侧车账号）"))
        # 人设自证：配置里指定的按群人设到底生效了没有。
        # （这个字段曾经"画在页面上但运行期没人读"，不打印就不出来）
        _cp = config_personas()
        log("P11: 人设 配置指定={} 生效群数={}".format(len(_cp), len(groups)))
        for _cid, _key in sorted(_cp.items()):
            log("P11: 人设 cid={} -> {}".format(_cid[:8], _key))
        # P12-A 自证：功能注册表的接线进度与"现在关掉了哪些"。
        # 与 P8/P11 同一个道理 —— "悄悄退回默认值/悄悄没接线"必须能一眼看出来。
        _fs = features.summary()
        log("P12: 功能 共={} 已接线={} 待接线={} 默认关={} 秒退={}".format(
            _fs["total"], _fs["wired"], _fs["planned"],
            ",".join(_fs["off_by_default"]) or "无",
            "开" if _fs["env_all_on"] else "关"))
        # P12-B 自证：参数总共几个、被覆盖了几个、按群集合各几个群。
        # 与功能自证同一个道理 —— "页面上改了、这里没生效"要能一眼看出来。
        _ts = tuning.summary()
        log("P12: 参数 共={} 已覆盖={} 集合={} 秒退={}".format(
            _ts["params"], _ts["overridden"],
            ",".join("{}:{}".format(k, v) for k, v in sorted(_ts["scopes"].items())),
            "开" if _ts["env_off"] else "关"))
        # P12-E 自证：人被改过人设文本/话术的**有几个人设、改了几处**。
        # 与上面同一个道理 —— "页面上改了、运行期没生效"要能一眼看出来。
        _ps = personas.summary()
        log("P12: 人设文本 覆盖人设={} 文本段={} 话术={} 体检={}".format(
            _ps["personas"], _ps["fields"], _ps["phrases"],
            "全部通过" if not _ps["issues"] else "有 {} 条提示".format(
                len(_ps["issues"]))))
        for _line in _ps["issues"]:
            log("P12: 人设体检 " + _line)
    except Exception as e:                                 # noqa: BLE001
        log("P11: 配置状态未知（" + str(e)[:60] + "）")
