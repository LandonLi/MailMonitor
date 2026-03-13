#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import imaplib
import email
import time
import ssl
import logging
import socket
import select
import requests
import os
import json
import sys
import re
from email.header import decode_header
from pathlib import Path
from urllib.parse import urlparse

# 尝试导入 PySocks 支持代理
try:
    import socks
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False

def setup_logger():
    logger = logging.getLogger("MailMonitor")
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    if logger.handlers:
        logger.handlers.clear()
        
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler(log_dir / "mail_monitor.log", encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    return logger

log = setup_logger()

UID_PATTERN = re.compile(r"\bUID\s+(\d+)\b", re.IGNORECASE)


def parse_proxy_settings(proxy_url):
    if not proxy_url:
        return None

    if not HAS_SOCKS:
        log.error("未安装 PySocks 库，无法启用代理！请执行: pip install PySocks")
        sys.exit(1)

    try:
        parsed = urlparse(proxy_url)
        scheme = (parsed.scheme or "").lower()

        if scheme in ("socks5", "socks5h"):
            proxy_type = socks.SOCKS5
        elif scheme == "socks4":
            proxy_type = socks.SOCKS4
        elif scheme in ("http", "https"):
            proxy_type = socks.HTTP
        else:
            raise ValueError(f"不支持的代理协议: {scheme or '<empty>'}")

        if not parsed.hostname or not parsed.port:
            raise ValueError("代理地址缺少主机名或端口")

        settings = {
            "proxy_type": proxy_type,
            "proxy_addr": parsed.hostname,
            "proxy_port": parsed.port,
            "proxy_username": parsed.username,
            "proxy_password": parsed.password,
            "proxy_rdns": True,
        }
        log.info(f"已启用网络代理 -> {proxy_url} (仅 IMAP/HTTP 请求通过此代理)")
        return settings
    except Exception as e:
        log.error(f"解析代理地址失败: {e}")
        sys.exit(1)


class ProxyMixin:
    def __init__(self, *args, proxy_settings=None, **kwargs):
        self.proxy_settings = proxy_settings
        super().__init__(*args, **kwargs)

    def _create_socket(self, timeout):
        if not self.proxy_settings:
            return super()._create_socket(timeout)

        if timeout is not None and not timeout:
            raise ValueError("Non-blocking socket (timeout=0) is not supported")

        return socks.create_connection(
            (self.host, self.port),
            timeout=timeout,
            **self.proxy_settings,
        )


class ProxyIMAP4(ProxyMixin, imaplib.IMAP4):
    pass


class ProxyIMAP4_SSL(ProxyMixin, imaplib.IMAP4_SSL):
    pass


class MailMonitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.imap = None
        self.idle_tag = 0
        self.last_seen_uid = None
        self.proxy_settings = parse_proxy_settings(cfg.get("proxy_url"))
        self.dry_run = bool(cfg.get("dry_run", False))

        # Requests 代理配置
        self.req_proxies = None
        if cfg.get("proxy_url"):
            self.req_proxies = {
                "http": cfg["proxy_url"],
                "https": cfg["proxy_url"]
            }
        
        self.IDLE_TIMEOUT = 29 * 60       
        self.HEARTBEAT_INTERVAL = int(cfg.get("heartbeat_interval", 15)) * 60 
        self.RECONNECT_DELAY = 10         

    def enable_tcp_keepalive(self):
        sock = self.imap.sock
        if not sock: return
        try:
            # 开启底层 Keepalive 探针，并极具攻击性地设置（针对恶劣的网络环境）
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, 'TCP_KEEPIDLE'):
                # 空闲 30 秒就开始发送保活探针
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
            if hasattr(socket, 'TCP_KEEPINTVL'):
                # 探针发送间隔 10 秒
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            if hasattr(socket, 'TCP_KEEPCNT'):
                # 连续 3 次失败才认为断开
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            log.info("TCP Keepalive (底层保活) 已开启")
        except Exception as e:
            # 使用代理时（如SocksSocket）可能不支持设置底层的 Keepalive 参数
            log.debug(f"跳过设置 TCP Keepalive (在使用代理时属于正常现象): {e}")

    def connect(self):
        while True:
            try:
                if self.imap:
                    try:
                        self.imap.logout()
                    except Exception:
                        pass

                log.info(f"正在连接到 IMAP 服务器 -> {self.cfg['imap_server']}:{self.cfg['imap_port']} ...")
                context = ssl.create_default_context()

                if self.cfg['imap_port'] == 993:
                    self.imap = ProxyIMAP4_SSL(
                        self.cfg['imap_server'],
                        self.cfg['imap_port'],
                        ssl_context=context,
                        proxy_settings=self.proxy_settings,
                    )
                else:
                    self.imap = ProxyIMAP4(
                        self.cfg['imap_server'],
                        self.cfg['imap_port'],
                        proxy_settings=self.proxy_settings,
                    )

                self.enable_tcp_keepalive()
                self.imap.login(self.cfg['username'], self.cfg['password'])
                log.info(f"账号 [{self.cfg['username']}] 登录成功")

                resp, _ = self.imap.select(self.cfg['folder'], readonly=False)
                if resp != "OK":
                    raise Exception(f"无法定位监控文件夹: [{self.cfg['folder']}]，请检查配置。")

                self.refresh_last_seen_uid()
                log.info(f"当前监控目标文件夹: [{self.cfg['folder']}]")
                log.info(f"运行模式 -> {'DRY_RUN (仅记录日志，不推送、不改已读)' if self.dry_run else 'NORMAL'}")
                return
            except Exception as e:
                log.error(f"连接失败: {e}，将在 {self.RECONNECT_DELAY} 秒后重试...")
                time.sleep(self.RECONNECT_DELAY)

    def send_noop(self):
        try:
            status, _ = self.imap.noop()
            if status == 'OK':
                log.info("NOOP 心跳发送成功，保持连接活跃")
                return True
            raise Exception("NOOP 响应不是 OK")
        except Exception as e:
            log.warning(f"心跳发送异常: {e}")
            return False

    def enter_idle(self):
        self.idle_tag += 1
        tag = f"A{self.idle_tag}"
        try:
            self.imap.send(f"{tag} IDLE\r\n".encode())
            resp = self.imap.readline().decode(errors="ignore").strip()
            if resp.startswith('+'):
                log.info("=== 成功进入 IDLE 待机监听状态 ===")
                return tag
            log.warning(f"进入 IDLE 失败，响应: {resp}")
            return None
        except Exception as e:
            log.error(f"发送 IDLE 指令异常: {e}")
            return None

    def exit_idle(self, tag):
        try:
            self.imap.send(b"DONE\r\n")
            while True:
                line = self.imap.readline()
                if not line:
                    break
                text = line.decode(errors="ignore").strip()
                if text.startswith(tag):
                    break
            return True
        except Exception:
            return False

    def refresh_last_seen_uid(self):
        status, data = self.imap.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            self.last_seen_uid = 0
            log.info("当前文件夹为空，UID 基线初始化为 0")
            return

        uid_list = [int(uid) for uid in data[0].split()]
        self.last_seen_uid = max(uid_list)
        log.info(f"UID 基线初始化完成，当前最新 UID = {self.last_seen_uid}")

    def search_new_uids(self):
        if self.last_seen_uid is None:
            self.refresh_last_seen_uid()

        status, data = self.imap.uid("search", None, f"UID {self.last_seen_uid + 1}:*")
        if status != "OK" or not data or not data[0]:
            return []

        return [int(uid) for uid in data[0].split()]

    def describe_new_uids(self, new_uids):
        if not new_uids:
            return "0 封"
        if len(new_uids) == 1:
            return f"1 封 (UID={new_uids[0]})"
        return f"{len(new_uids)} 封 (UID={new_uids[0]}..{new_uids[-1]})"

    def fetch_header_by_uid(self, uid):
        status, msg_data = self.imap.uid("fetch", str(uid), "(BODY.PEEK[HEADER] UID)")
        if status != "OK" or not msg_data:
            return None, None

        raw_email = None
        fetched_uid = uid
        for item in msg_data:
            if not isinstance(item, tuple):
                continue
            meta, payload = item
            raw_email = payload or raw_email
            meta_text = meta.decode(errors="ignore") if isinstance(meta, bytes) else str(meta)
            match = UID_PATTERN.search(meta_text)
            if match:
                fetched_uid = int(match.group(1))

        return fetched_uid, raw_email

    def mark_seen_by_uid(self, uid):
        if self.dry_run:
            log.info(f"DRY_RUN: 跳过将 UID={uid} 标记为已读")
            return
        self.imap.uid("store", str(uid), "+FLAGS", "(\\Seen)")

    def handle_idle_line(self, line):
        text = line.decode(errors="ignore").strip()
        if not text:
            return None
        if "BYE" in text.upper():
            log.warning(f"服务器主动断开连接: {text}")
            return "ERROR"
        if "EXISTS" in text.upper() or "RECENT" in text.upper():
            log.info(f"[*] 检测到服务器信箱变化信号: {text}")
            return "NEW_MAIL"
        return None

    def wait_for_idle_events(self, tag):
        start = time.time()
        last_heartbeat = time.time()
        sock = self.imap.sock

        while True:
            now = time.time()
            if now - last_heartbeat > self.HEARTBEAT_INTERVAL:
                self.exit_idle(tag)
                return "HEARTBEAT"

            if now - start > self.IDLE_TIMEOUT:
                log.info("已达到单次 IDLE 协议最大时长，准备刷新连接...")
                self.exit_idle(tag)
                return "TIMEOUT"

            try:
                readable, _, _ = select.select([sock], [], [], 1.0)
                if not readable:
                    continue

                line = self.imap.readline()
                if not line:
                    log.warning("Socket 收到空数据 (EOF)，连接已被防火墙或对端悄悄切断。")
                    return "ERROR"

                event = self.handle_idle_line(line)
                if event:
                    self.exit_idle(tag)
                    return event
            except (OSError, ValueError) as e:
                log.error(f"Socket 监听异常中断: {e}")
                return "ERROR"
            except (socket.timeout, TimeoutError):
                continue
            except ssl.SSLError as e:
                if "timed out" in str(e).lower():
                    continue
                log.error(f"SSL 监听异常中断: {e}")
                return "ERROR"
            except imaplib.IMAP4.abort as e:
                log.error(f"IMAP 连接异常中断: {e}")
                return "ERROR"

    def decode_mime_words(self, s):
        if not s:
            return ""
        try:
            return ''.join(
                word.decode(encoding or 'utf-8') if isinstance(word, bytes) else word
                for word, encoding in decode_header(str(s))
            )
        except Exception:
            return str(s)

    def process_new_mail(self):
        try:
            self.imap.noop()
            new_uids = self.search_new_uids()
            if not new_uids:
                log.info(f"本次信箱变化未发现新增 UID，当前基线 UID = {self.last_seen_uid}")
                return

            log.info(f"发现新增邮件 -> {self.describe_new_uids(new_uids)}")

            for uid in new_uids:
                fetched_uid, raw_email = self.fetch_header_by_uid(uid)
                if not raw_email:
                    log.warning(f"拉取 UID={uid} 的邮件头失败，已跳过")
                    continue
                msg = email.message_from_bytes(raw_email)

                subject = self.decode_mime_words(msg.get("Subject") or "无标题")
                sender = self.decode_mime_words(msg.get("From") or "未知发件人")

                log.info(f"解析提取 -> UID: [{fetched_uid}] 主题: [{subject}] 发件人: [{sender}]")
                self.send_pushover(f"新邮件: {sender}", subject)
                self.mark_seen_by_uid(fetched_uid)
                self.last_seen_uid = max(self.last_seen_uid or 0, fetched_uid)
        except Exception as e:
            log.error(f"处理邮件过程中出错: {e}")

    def send_pushover(self, title, message):
        token = self.cfg.get('pushover_token')
        user = self.cfg.get('pushover_user')
        if not token or not user:
            log.warning("未配置 Pushover 凭证，跳过推送")
            return

        if self.dry_run:
            log.info(f"DRY_RUN: 跳过 Pushover 推送 -> title=[{title}] message=[{message}]")
            return

        try:
            resp = requests.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": token,
                    "user": user,
                    "title": title,
                    "message": message
                },
                proxies=self.req_proxies,
                timeout=10
            )
            if resp.status_code == 200:
                log.info("手机推送 (Pushover) 成功投递")
            else:
                log.error(f"Pushover 推送失败，状态码: {resp.status_code}，响应: {resp.text}")
        except Exception as e:
            log.error(f"Pushover 请求异常: {e}")

    def run(self):
        log.info("邮件监控服务启动")
        while True:
            self.connect()
            while True:
                tag = self.enter_idle()
                if not tag:
                    break 

                event = self.wait_for_idle_events(tag)

                if event == "NEW_MAIL":
                    self.process_new_mail()
                elif event == "HEARTBEAT":
                    if not self.send_noop():
                        break 
                elif event == "TIMEOUT":
                    continue 
                else:
                    log.warning("检测到连接异常或断开，准备重连...")
                    break 
            
            time.sleep(3) 


def load_config():
    config_file = os.getenv("CONFIG_FILE", "/app/config.json")
    if not os.path.exists(config_file):
        config_file = "config.json" 

    config_data = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            log.info(f"读取到配置文件 -> {config_file}")
        except Exception as e:
            log.error(f"解析配置文件 {config_file} 失败: {e}")

    def get_val(env_key, json_key, default=None):
        return os.getenv(env_key, config_data.get(json_key, default))

    def get_bool_val(env_key, json_key, default=False):
        raw = os.getenv(env_key)
        if raw is None:
            raw = config_data.get(json_key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    cfg = {
        "imap_server": get_val("IMAP_SERVER", "imap_server"),
        "imap_port": int(get_val("IMAP_PORT", "imap_port", 993)),
        "username": get_val("EMAIL_USERNAME", "username"),
        "password": get_val("EMAIL_PASSWORD", "password"),
        "folder": get_val("MAIL_FOLDER", "folder", "INBOX"),
        "pushover_token": get_val("PUSHOVER_APP_TOKEN", "pushover_app_token"),
        "pushover_user": get_val("PUSHOVER_USER_KEY", "pushover_user_key"),
        "proxy_url": get_val("PROXY_URL", "proxy_url"),
        "heartbeat_interval": int(get_val("HEARTBEAT_INTERVAL", "heartbeat_interval", 15)),
        "dry_run": get_bool_val("DRY_RUN", "dry_run", False),
    }

    missing = [k for k in ["imap_server", "username", "password"] if not cfg[k]]
    if missing:
        log.error(f"缺少启动监控的必要配置: {', '.join(missing)}")
        log.error("请使用环境变量或 config.json 进行配置！")
        sys.exit(1)

    return cfg


if __name__ == "__main__":
    try:
        cfg = load_config()
        monitor = MailMonitor(cfg)
        monitor.run()
    except KeyboardInterrupt:
        log.info("进程被用户主动终止。")
    except Exception as e:
        log.error(f"服务异常退出: {e}", exc_info=True)
