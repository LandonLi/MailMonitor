# MailMonitor - IMAP IDLE 邮件实时监控推送

基于 IMAP IDLE 机制的轻量级 Python 邮件监控器。专为 VPS 与 Docker 环境优化，能够实时监听指定邮箱文件夹的变动，通过 Pushover 将新邮件通知推送至手机，并自动将其标记为已读。

## ✨ 核心特性

- **极致实时 (IMAP IDLE)**：采用长连接监听替代定时轮询，新邮件到达后立即处理。
- **VPS 网络优化**：开启 TCP Keepalive 并在应用层配置 15 分钟 NOOP 心跳保活，29 分钟强制刷新机制，完美适应严格的防火墙环境。
- **代理隔离更清晰**：内置 PySocks 支持 SOCKS5/HTTP 代理，仅作用于 IMAP 与 Pushover 请求，不再全局改写 `socket`。
- **Docker 优先**：开箱即用的容器化部署，非 root 用户运行，确保极高的系统隔离与安全性。
- **配置双轨制**：支持 `.env` 环境变量注入与 `config.json` 静态文件读取。
- **UID 增量处理**：启动时记录当前文件夹的最新 UID，只推送后续真正新增的邮件，不会扫掉历史未读。
- **可观察的调试模式**：支持 `DRY_RUN=true`，仅记录日志，不推送、不改已读，适合上线前观察。

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
> **注意：** 项目默认会将日志挂载至宿主机的 `./logs` 目录，方便你直接通过 `cat logs/mail_monitor.log` 排查历史问题。

### 3. 查看实时运行日志
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
| `HEARTBEAT_INTERVAL`| ❌ | 15 | NOOP 心跳间隔(分钟)。若频繁由于防火墙导致连接中断(EOF)，可调小至 9 |
| `DRY_RUN` | ❌ | false | 调试模式。设为 `true` 后只记录日志，不会推送通知，也不会把邮件标记为已读 |

## 🛠️ 本地 Python 运行

如果你不想使用 Docker，也可以直接在物理机上运行：

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备配置
cp config.example.json config.json
nano config.json # 填入配置

# 3. 运行
python3 mail_monitor.py
```

## ⚠️ 常见问题

- **Q: 为什么 Gmail 提示密码错误？**
  A: Gmail 强制要求第三方客户端使用“应用专用密码”。请前往 Google 账号设置中开启两步验证，并生成一个 16 位的应用密码填入 `EMAIL_PASSWORD`。

- **Q: 日志提示 `Socket 收到空数据 (EOF)` 或连接被切断？**
  A: 这通常意味着你的云服务器或运营商网关对 TCP 长连接极其严苛，在空闲时强行切断了链路。程序已针对此场景优化：会自动标记异常并触发秒级重连恢复，属于正常抵抗网络波动的现象。如果重连过于频繁（每 15 分钟一次），请尝试将 `HEARTBEAT_INTERVAL` 调小。

- **Q: 为什么启动后没有把邮箱里原本的未读邮件全部推送出来？**
  A: 程序现在按 UID 增量工作。首次启动会把当前文件夹的最新 UID 作为基线，只推送启动之后新到达的邮件，避免把历史未读一次性全部标记为已读。

- **Q: 想先观察行为，不想真的推送或改已读怎么办？**
  A: 将 `DRY_RUN=true`。程序仍会连接 IMAP 并解析新邮件，但只打印日志，不会调用 Pushover，也不会写入 `\Seen` 标记。

- **Q: 如何排查连不上网/获取不到文件夹？**
  A: 程序启动时若连接失败会打印异常日志；若配置了代理请确认代理可用；另外检查 `MAIL_FOLDER` 的名称是否与你服务商真实存在的目录（如部分服务商归档目录为 `&XfJT0ZAB-` 这种编码格式）完全一致。
