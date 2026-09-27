# tools/legacy —— 一次性开发脚本（**不是生产代码**）

这里放的是"当初为了排一个问题临时写的"脚本：查消息、抓 socket、试发送、
试刷新登录态。它们**没有任何地方引用**，也不进镜像、不参与部署。

判定标准（2026-09-27 体检）：在整个仓库里（py / sh / yml / service / ps1 / md）
**一处都没被引用**，才挪进来。

> ⚠️ 与"运维手工工具"的区别：像 `batch_register.py`、`captcha_novnc.py`、
> `manual_login.py`、`renew_token_browser.py`、`regen_novel.py` 这些虽然也不是被
> import 的，但**文档/脚本里写了怎么手工跑**，所以仍留在仓库根目录 ——
> 挪动它们要先改文档，属于另一个话题。

## 目录内容

| 文件 | 当初用来做什么 |
| --- | --- |
| `_check_msgs.py` | 手工打印某个群的最近消息（调试读消息链路） |
| `switch_node.py` | 切换平台线路/节点（后来改成 `.env` 的 `DZMM_HOST`） |
| `test_private.py` / `test_private_diag.py` | 私聊发送与私聊 chat_id 的排查 |
| `test_refresh.py` / `test_reload.py` | 页面刷新 / 重新加载的排查 |
| `test_send_compare.py` | 对比"账号通道 vs 官方 Bot 通道"的发送结果 |
| `test_sio_diag.py` | Socket.IO 连接诊断 |

**要恢复使用**：直接 `python tools/legacy/<名字>.py`；需要它进镜像/部署的话，
说明它已经变成生产工具了 —— 请挪回仓库根目录并在 `docs/` 里写清用法。
