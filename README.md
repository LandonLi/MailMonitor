# MailMonitor - IMAP IDLE 邮件实时监控推送

基于 IMAP IDLE 机制的轻量级 Python 邮件监控器。专为 VPS 与 Docker 环境优化，能够毫秒级实时监听指定邮箱文件夹的变动，通过 Pushover 将新邮件通知推送至手机，并自动将其标记为已读。

## ✨ 核心特性

- **极致实时 (IMAP IDLE)**：采用底层的 IMAP 长连接阻塞监听，拒绝低效的定时轮询，新邮件秒级推送。
- **VPS 网络优化**：开启 TCP Keepalive 并在应用层配置 15 分钟 NOOP 心跳保活，29 分钟强制刷新机制，完美适应严格的防火墙环境。
- **全端代理支持**：内置 PySocks 支持全局 SOCKS5/HTTP 代理，无惧邮件服务商地域风控。
- **Docker 优先**：开箱即用的容器化部署，非 root 用户运行，确保极高的系统隔离与安全性。
- **配置双轨制**：支持 `.env` 环境变量注入与 `config.json` 静态文件读取。

## 🚀 快速启动 (Docker 推荐)

本项目强烈推荐使用 Docker Compose 进行部署。

### 1. 配置参数
你可以通过环境变量（`.env`）或 JSON 文件配置，程序会优先读取环境变量。

克隆或下载项目后，复制示例环境文件：
```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的配置信息：
```env
# IMAP 服务器配置
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
EMAIL_USERNAME=your_email@example.com
EMAIL_PASSWORD=your_app_password
MAIL_FOLDER=INBOX

# Pushover 推送配置
PUSHOVER_APP_TOKEN=your_pushover_app_token
PUSHOVER_USER_KEY=your_pushover_user_key

# 网络代理 (可选，支持 SOCKS5 或 HTTP)
# PROXY_URL=socks5://127.0.0.1:1080
```

### 2. 启动服务
```bash
docker-compose up -d --build
```

### 3. 查看运行状态
```bash
docker logs -f mail-monitor
```

## ⚙️ 常见配置项说明

| 变量名 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `IMAP_SERVER` | ✅ | - | 邮件服务器地址 (如 `imap.qq.com`, `imap.gmail.com`) |
| `IMAP_PORT` | ❌ | 993 | IMAP 端口，993 为 SSL 安全连接 |
| `EMAIL_USERNAME`| ✅ | - | 邮箱完整账号 |
| `EMAIL_PASSWORD`| ✅ | - | 密码。**重要：** Gmail/QQ等请使用**应用专用授权码** |
| `MAIL_FOLDER` | ❌ | INBOX | 需要监听的文件夹名称，不同服务商可能有前缀差异 |
| `PUSHOVER_APP_TOKEN`| ✅ | - | 手机端 Pushover 申请的 App 令牌 |
| `PUSHOVER_USER_KEY` | ✅ | - | 手机端 Pushover 的用户识别码 |
| `PROXY_URL` | ❌ | - | 设置代理。格式：`socks5://ip:port` 或 `http://ip:port` |
| `HEARTBEAT_INTERVAL`| ❌ | 15 | NOOP 心跳间隔(分钟)。若频繁断线(EOF)可将其调小，如 9 |

## 🛠️ 本地 Python 运行

如果你不想使用 Docker，也可以直接在物理机上运行：

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备配置
cp config.example.json config.json
nano config.json # 填入配置

# 3. 运行
python mail_monitor.py
```

## ⚠️ 常见问题

- **Q: 为什么 Gmail 提示密码错误？**
  A: Gmail 强制要求第三方客户端使用“应用专用密码”。请前往 Google 账号设置中开启两步验证，并生成一个 16 位的应用密码填入 `EMAIL_PASSWORD`。

- **Q: 为什么日志提示 `socket error: TLS/SSL connection has been closed (EOF)`？**
  A: 你的云服务器或运营商网关对 TCP 长连接极其严苛，强行切断了空闲链路。程序会自动秒级重连恢复，属于正常抵抗网络波动的现象。如果频繁发生，可自行在代码中将 `HEARTBEAT_INTERVAL` 调小。

- **Q: 如何排查连不上网/获取不到文件夹？**
  A: 程序启动时若连接失败会打印异常日志；若配置了代理请确认代理可用；另外检查 `MAIL_FOLDER` 的名称是否与你服务商真实存在的目录（如部分服务商归档目录为 `&XfJT0ZAB-` 这种编码格式）完全一致。