#!/usr/bin/env python3
"""
发送邮件脚本 - 用于 bilibili-to-obsidian 工作流
用法: python send_email.py <收件人> <主题> <内容文件路径> [--html]
"""

import smtplib
import sys
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email(to_addr, subject, content, from_addr=None, smtp_server='smtp.qq.com', smtp_port=587, html=False):
    """发送邮件"""
    # 从环境变量或配置文件读取发件人信息
    if from_addr is None:
        from_addr = os.environ.get('EMAIL_FROM')
    if not from_addr:
        print("错误: 未配置发件人")
        print("请设置 EMAIL_FROM；邮件发送默认关闭，只有显式调用本脚本时才会发送")
        return False

    # 读取授权码
    auth_code = os.environ.get('EMAIL_AUTH_CODE')
    if not auth_code:
        # 仅读取用户显式指定的配置文件；不探测 Hermes/Codex 私有目录。
        config_path = os.environ.get('EMAIL_AUTH_FILE')
        if config_path and os.path.exists(os.path.expanduser(config_path)):
            import json
            with open(os.path.expanduser(config_path), encoding='utf-8') as f:
                config = json.load(f)
                auth_code = config.get('auth_code')

    if not auth_code:
        print("错误: 未配置邮件授权码")
        print("请设置 EMAIL_AUTH_CODE，或通过 EMAIL_AUTH_FILE 指定本机配置文件")
        print("配置格式: {\"from\": \"your@email.com\", \"auth_code\": \"your_auth_code\"}")
        return False

    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(content, 'html' if html else 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(from_addr, auth_code)
        server.send_message(msg)
        server.quit()
        print(f"邮件发送成功: {to_addr}")
        return True
    except Exception as e:
        print(f"发送失败: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("用法: python send_email.py <收件人> <主题> <内容文件路径> [--html]")
        sys.exit(1)

    to_addr = sys.argv[1]
    subject = sys.argv[2]
    content_file = sys.argv[3]
    html = '--html' in sys.argv[4:]

    with open(content_file, 'r', encoding='utf-8') as f:
        content = f.read()

    send_email(to_addr, subject, content, html=html)
