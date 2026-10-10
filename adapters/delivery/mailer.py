"""SMTP 邮件投递适配器：发邮件 / 测连接。

和 `adapters/delivery/telegram.py` 同一层级，但协议完全不同——SMTP 不是
HTTP，没法走 `caps/httpfetch`，这里直接用标准库 `smtplib`。测试注入点
从 `transport` 换成 `smtp_class`（默认按 `use_tls` 在 `smtplib.SMTP`
——之后调 `starttls()`——和 `smtplib.SMTP_SSL`——隐式 TLS——之间二选一），
理由和 `caps/httpfetch` 的 `transport` 参数一致：生产路径走真实网络，
测试路径换成不发真实邮件的替身类。
"""

from __future__ import annotations

import smtplib
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any

from caps.probe import ProbeResult, fail, ok

_PROBE_SUBJECT = "literature-digest：连接测试"
_PROBE_BODY = "这是一条测试邮件，确认 SMTP 连接配置正确。"

_REQUIRED_CONFIG_KEYS = ("host", "port", "username", "password", "from_address", "to_address")


class MailerError(Exception):
    """连接 / 认证 / 发送失败（`smtplib` 的异常或网络层 `OSError` 统一
    包成这一种，调用方不需要认识 `smtplib` 的异常层级）。"""


def send_message(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    from_address: str,
    to_address: str,
    subject: str,
    body: str,
    use_tls: bool = True,
    smtp_class: type | None = None,
) -> None:
    """发一封纯文本邮件。`username` 为空时跳过 `login()`——有些内部 SMTP
    中继不需要认证，和 `adapters.sources.pubmed` 的 `api_key` 可选同一种
    "没配就跳过那一步，不强求"的处理方式。
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = to_address
    message.set_content(body)

    client_class = smtp_class or (smtplib.SMTP if use_tls else smtplib.SMTP_SSL)
    try:
        with client_class(host, port, timeout=10) as client:
            if use_tls:
                client.starttls()
            if username:
                client.login(username, password)
            client.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailerError(f"发送邮件失败：{exc}") from exc


def check(
    config: Mapping[str, Any] | None,
    *,
    smtp_class: type | None = None,
) -> ProbeResult:
    """满足 `caps.probe.CheckFn` 协议。`config` 需要 host/port/username/
    password/from_address/to_address 六个字段都配齐——`to_address` 用来
    发测试邮件，和 `adapters.delivery.telegram.check` 发测试消息同一个
    理由。缺字段直接返回失败结果，不抛异常。
    """
    config = config or {}
    missing = [key for key in _REQUIRED_CONFIG_KEYS if not config.get(key)]
    if missing:
        return fail(f"缺少配置：{', '.join(missing)}")
    try:
        send_message(
            host=config["host"],
            port=int(config["port"]),
            username=config["username"],
            password=config["password"],
            from_address=config["from_address"],
            to_address=config["to_address"],
            subject=_PROBE_SUBJECT,
            body=_PROBE_BODY,
            use_tls=config.get("use_tls", True),
            smtp_class=smtp_class,
        )
    except MailerError as exc:
        return fail(str(exc))
    return ok("SMTP 连接正常，已发送一封测试邮件")
