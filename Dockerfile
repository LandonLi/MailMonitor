FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量，确保 Python 日志能够直接在 docker logs 中实时显示而不被缓冲
ENV PYTHONUNBUFFERED=1

# 复制依赖并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制核心源码
COPY mail_monitor.py .

# 创建一个非 root 用户执行，增强容器安全性，并配置日志挂载目录权限
RUN useradd -m appuser && \
    mkdir -p /app/logs && \
    chown -R appuser:appuser /app
USER appuser

# 启动命令
CMD ["python", "mail_monitor.py"]