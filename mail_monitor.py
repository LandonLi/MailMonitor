#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import imaplib
import email
import time
import ssl
import logging
import socket
import requests
import os
import json
import sys
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

def configure_global_proxy(proxy_url):
    """配置全局 Socket 代理 (用于 IMAP 连接)"""
    if not proxy_url:
        return
        
    if not HAS_SOCKS:
        log.error("未安装 PySocks 库，无法启用代理！请执行: pip install PySocks")
        sys.exit(1)
        
    try:
        parsed = urlparse(proxy_url)
        scheme = parsed.scheme.lower()
        
        if scheme in ('socks5', 'socks5h'):
            proxy_type = socks.SOCKS5
        elif scheme == 'socks4':
            proxy_type = socks.SOCKS4
        elif scheme in ('http', 'https'):
            proxy_type = socks.HTTP
        else:
            log.warning(f"不支持的代理协议: {scheme}，代理可能无法正常工作。")
            return
            
        kwargs = {
            'proxy_type': proxy_type,
            'addr': parsed.hostname,
            'port': parsed.port,
            'rdns': True  # 关键：开启远程 DNS 解析，防止本地解析污染分流策略
        }
        if parsed.username:
            kwargs['username'] = parsed.username
            kwargs['password'] = parsed.password
            
        socks.set_default_proxy(**kwargs)
        socket.socket = socks.socksocket
        log.info(f"已启用网络代理 -> {proxy_url} (IMAP 将通过此代理连接)")
    except Exception as e:
        log.error(f"解析代理地址失败: {e}")
        sys.exit(1)


class MailMonitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.imap = None
        self.idle_tag = 0
        
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
                    try: self.imap.logout()
                    except: pass
                
                log.info(f"正在连接到 IMAP 服务器 -> {self.cfg['imap_server']}:{self.cfg['imap_port']} ...")
                context = ssl.create_default_context()
                
                if self.cfg['imap_port'] == 993:
                    self.imap = imaplib.IMAP4_SSL(self.cfg['imap_server'], self.cfg['imap_port'], ssl_context=context)
                else:
                    self.imap = imaplib.IMAP4(self.cfg['imap_server'], self.cfg['imap_port'])
                
                self.enable_tcp_keepalive()
                self.imap.login(self.cfg['username'], self.cfg['password'])
                log.info(f"账号 [{self.cfg['username']}] 登录成功")
                
                resp, _ = self.imap.select(self.cfg['folder'], readonly=False)
                if resp != "OK":
                    raise Exception(f"无法定位监控文件夹: [{self.cfg['folder']}]，请检查配置。")
                
                log.info(f"当前监控目标文件夹: [{self.cfg['folder']}]")
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
            resp = self.imap.readline().decode()
            if "+ idling" in resp.lower() or resp.startswith('+'):
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
                line = self.imap.readline().decode(errors="ignore")
                if tag in line or not line:
                    break
            return True
        except:
            return False

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
                # 使用代理时，某些底层实现可能会在 recv 时抛出 TimeoutError 而不是 socket.timeout
                sock.settimeout(1.0)
                data = sock.recv(4096)
                if data:
                    text = data.decode(errors="ignore")
                    if "EXISTS" in text or "RECENT" in text:
                        log.info(f"[*] 检测到服务器信箱变化信号")
                        self.exit_idle(tag)
                        return "NEW_MAIL"
            except (socket.timeout, TimeoutError):
                # 正常超时，继续下一轮循环以检查心跳
                continue
            except ssl.SSLError as e:
                # 捕获 SSL 错误中的 The read operation timed out
                if "timed out" in str(e).lower():
                    continue
                log.error(f"SSL 监听异常中断: {e}")
                return "ERROR"
            except Exception as e:
                log.error(f"Socket 监听异常中断: {e}")
                return "ERROR"

    def decode_mime_words(self, s):
        if not s:
            return ""
        try:
            return ''.join(
                word.decode(encoding or 'utf-8') if isinstance(word, bytes) else word
                for word, encoding in decode_header(str(s))
            )
        except:
            return str(s)

    def process_new_mail(self):
        try:
            self.imap.noop()
            resp, data = self.imap.search(None, "UNSEEN")
            if resp != "OK" or not data or not data[0]:
                return

            msg_ids = data[0].split()
            log.info(f"拉取到 {len(msg_ids)} 封未读新邮件")

            for msg_id in msg_ids:
                typ, msg_data = self.imap.fetch(msg_id, "(RFC822.HEADER)")
                if typ != "OK": continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                
                subject = self.decode_mime_words(msg.get("Subject") or "无标题")
                sender = self.decode_mime_words(msg.get("From") or "未知发件人")

                log.info(f"解析提取 -> 主题: [{subject}] 发件人: [{sender}]")
                self.send_pushover(f"新邮件: {sender}", subject)

                self.imap.store(msg_id, "+FLAGS", "\\Seen")
        except Exception as e:
            log.error(f"处理邮件过程中出错: {e}")

    def send_pushover(self, title, message):
        token = self.cfg.get('pushover_token')
        user = self.cfg.get('pushover_user')
        if not token or not user:
            log.warning("未配置 Pushover 凭证，跳过推送")
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

    cfg = {
        "imap_server": get_val("IMAP_SERVER", "imap_server"),
        "imap_port": int(get_val("IMAP_PORT", "imap_port", 993)),
        "username": get_val("EMAIL_USERNAME", "username"),
        "password": get_val("EMAIL_PASSWORD", "password"),
        "folder": get_val("MAIL_FOLDER", "folder", "INBOX"),
        "pushover_token": get_val("PUSHOVER_APP_TOKEN", "pushover_app_token"),
        "pushover_user": get_val("PUSHOVER_USER_KEY", "pushover_user_key"),
        "proxy_url": get_val("PROXY_URL", "proxy_url"),
        "heartbeat_interval": int(get_val("HEARTBEAT_INTERVAL", "heartbeat_interval", 15))
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
        
        # 如果配置了代理，在一切网络请求开始前配置全局 Socket 代理
        if cfg.get("proxy_url"):
            configure_global_proxy(cfg["proxy_url"])
            
        monitor = MailMonitor(cfg)
        monitor.run()
    except KeyboardInterrupt:
        log.info("进程被用户主动终止。")
    except Exception as e:
        log.error(f"服务异常退出: {e}", exc_info=True)