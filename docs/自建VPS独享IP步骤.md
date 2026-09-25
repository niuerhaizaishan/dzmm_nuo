# 自建 VPS 独享 IP —— 完整操作步骤

> 目标：给 bot 一个独享、干净的海外 IP，替代机场共享 IP（当前出口 `141.11.149.88` 香港 Akile），降低 DZMM 触发 captcha 的概率。
>
> 核心前提：dzmm.ai 国内直连 HTTP 000（被墙），必须海外 IP 才能访问，所以「干净 IP」= 独享的海外 VPS IP。

---

## 阶段一：买 VPS

### 选型（参考价，以官网实时为准）

| 服务商 | 地区 | 参考价 | 备注 |
|--------|------|--------|------|
| 阿里云国际 | 香港 | ~24-34 元/月 | **首选**：最稳、国内访问快 |
| 腾讯云国际 | 香港/新加坡 | ~25-50 元/月 | 备选 |
| Vultr | 日本/新加坡 | $6/月 | 需外币卡 |

**关键点：**
- 地区选 **香港**（延迟 40-60ms 最低，bot 收发消息快）
- 系统选 **Ubuntu 22.04 / Debian 12**
- 记下 VPS 的**公网 IP**

---

## 阶段二：VPS 上搭代理

用 **3x-ui 面板**（xray 全家桶 + 中文 web 界面，管理节点最省事）。

SSH 登录 VPS 后，一条命令装好：

```bash
bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
```

装完按提示操作：

1. 浏览器打开 `http://VPS公网IP:54321`（3x-ui 面板）
2. 登录（安装脚本最后会显示账号密码）
3. 「入站列表」→「添加入站」
4. 协议选 **Shadowsocks**（简单稳定）或 **VLess**
5. 记下三个字段：**端口 port、密码 password、加密 cipher**（例如 port=8388, cipher=aes-256-gcm）

### 防火墙（必做，否则连不上）

两层都要放行代理端口（如 8388）：
- **云服务商安全组**：控制台里开放 8388/TCP
- **系统内防火墙**：
  ```bash
  ufw allow 8388/tcp        # Ubuntu 用 ufw
  # 或
  firewall-cmd --add-port=8388/tcp --permanent && firewall-cmd --reload  # CentOS
  ```

---

## 阶段三：FlClash 接入自建节点

### 方式 A：订阅链接（推荐，最省事）

1. 3x-ui 面板 → 入站列表 → 订阅链接 → 选 **Clash 格式** → 复制链接
2. FlClash → 添加新订阅（与「青瓷」机场订阅**并存**，互不影响）
3. 刷新后会在节点列表出现自建节点

> 用独立订阅的好处：刷新自建订阅不影响青瓷，刷新青瓷也不覆盖自建节点。

### 方式 B：手动加节点（备用）

编辑 `~/Library/Application Support/com.follow.clash/config.yaml`：

在 `proxies:` 下加一个节点：
```yaml
  - name: "自建VPS-香港"
    type: ss
    server: "VPS公网IP"
    port: 8388
    cipher: aes-256-gcm
    password: "你的密码"
```

然后在 `proxy-groups:` 的「青瓷」组 proxies 列表里加一行：
```yaml
      - "自建VPS-香港"
```

> ⚠️ 坑：这个 config.yaml 是青瓷订阅生成的，**手动加节点后别再刷新青瓷订阅**，否则手动节点会被覆盖。所以优先用方式 A。

---

## 阶段四：切换 + 验证

1. FlClash 切到「自建VPS-香港」节点
2. 验证出口 IP 变了：
   ```bash
   curl -x http://127.0.0.1:7890 https://ipinfo.io/ip
   ```
   应显示 VPS 公网 IP（**不再是 141.11.149.88**）
3. 验证 dzmm.ai 访问正常：
   ```bash
   curl -x http://127.0.0.1:7890 -o /dev/null -w "%{http_code}\n" https://www.dzmm.ai
   ```
   应返回 **200**
4. 重启 bot（走 7890，**bot 代码零改动**），观察正常收发消息

---

## 阶段五：固定 + 风险提示

1. **固定用这个 IP**，别频繁切回机场节点——IP 突然跳地区会触发 DZMM 异地登录风控，比 captcha 更难缠
2. 代理务必加密码认证（3x-ui 默认带），别裸奔防被扫到盗用
3. 香港数据中心 IP 段也可能被 DZMM 标记，但**独享比机场共享干净得多**（机场 IP 会被陌生人连累，独享只受自己行为影响）
4. 若香港 VPS 仍频繁触发 captcha，再升级到静态住宅代理（家宽 IP，最真实但贵 $15-50/月）

---

## 备忘：bot 代理地址分布（切 IP 只动 FlClash，不用改这些）

| 文件 | 位置 | 用途 |
|------|------|------|
| bot.py | 第 105 行 | Playwright `proxy={"server": "http://127.0.0.1:7890"}` |
| wx_alert.py | 第 23 行 | 微信告警 PROXIES |
| online_status.py | 第 22 行 | 在线状态 PROXY |
| sio_subscribe.py | 第 31 行 | Socket.IO 读消息 PROXY |
| dzmm.py | 第 11 行 | DZMMSocket 发送 PROXIES |

> 全部指向本地 `127.0.0.1:7890`，只要 FlClash 出口切换，5 处自动生效。
