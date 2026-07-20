#!/usr/bin/env python3
"""Send PreMem experiment notifications through an SMTP account.

Secrets are read from environment variables and are never stored in this file.
The script only uses the Python standard library.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import socket
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable


PROVIDERS = {
    "qq": ("smtp.qq.com", 465, "ssl"),
    "163": ("smtp.163.com", 465, "ssl"),
    "gmail": ("smtp.gmail.com", 465, "ssl"),
    "outlook": ("smtp.office365.com", 587, "starttls"),
}
VALID_SECURITY = {"ssl", "starttls", "plain"}


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def split_addresses(values: Iterable[str]) -> list[str]:
    addresses: list[str] = []
    for value in values:
        for item in value.replace(";", ",").split(","):
            item = item.strip()
            if item and item not in addresses:
                addresses.append(item)
    return addresses


def infer_provider(user: str) -> str:
    domain = user.rsplit("@", 1)[-1].lower() if "@" in user else ""
    if domain in {"qq.com", "foxmail.com"}:
        return "qq"
    if domain in {"163.com", "126.com"}:
        return "163"
    if domain in {"gmail.com", "googlemail.com"}:
        return "gmail"
    if domain in {"outlook.com", "hotmail.com", "live.com"}:
        return "outlook"
    return "custom"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send an SMTP email for a PreMem experiment."
    )
    parser.add_argument(
        "--to",
        action="append",
        default=[],
        help="Recipient address; repeat or comma-separate for multiple recipients.",
    )
    parser.add_argument("--subject", default="PreMem server notification")
    body = parser.add_mutually_exclusive_group()
    body.add_argument("--body", default="")
    body.add_argument("--body-file", type=Path)
    parser.add_argument(
        "--attach",
        action="append",
        default=[],
        type=Path,
        help="File attachment; may be repeated.",
    )
    parser.add_argument(
        "--provider",
        choices=[*PROVIDERS, "custom"],
        default=env_first("EMAIL_PROVIDER", default=""),
    )
    parser.add_argument("--host", default=env_first("SMTP_HOST"))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--security",
        choices=sorted(VALID_SECURITY),
        default=env_first("SMTP_SECURITY", default=""),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(env_first("SMTP_TIMEOUT", default="20")),
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration without opening a network connection.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the message and print a safe summary without sending.",
    )
    return parser.parse_args()


def load_body(args: argparse.Namespace) -> str:
    if args.body_file:
        if not args.body_file.is_file():
            raise ValueError(f"Body file does not exist: {args.body_file}")
        return args.body_file.read_text(encoding="utf-8")
    if args.body:
        return args.body
    return (
        f"PreMem notification from {socket.gethostname()}.\n"
        "The email notification script is working."
    )


def attach_files(message: EmailMessage, paths: Iterable[Path]) -> None:
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Attachment does not exist: {path}")
        content_type, _ = mimetypes.guess_type(path.name)
        if content_type:
            maintype, subtype = content_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )


def main() -> int:
    args = parse_args()
    user = env_first("EMAIL_USER", "SMTP_USER")
    password = env_first("EMAIL_AUTH_CODE", "SMTP_PASSWORD")
    sender = env_first("EMAIL_FROM", default=user)
    recipients = split_addresses(
        [*args.to, env_first("EMAIL_TO", default="")]
    )

    provider = args.provider or infer_provider(user)
    if provider in PROVIDERS:
        preset_host, preset_port, preset_security = PROVIDERS[provider]
    else:
        preset_host, preset_port, preset_security = "", 0, ""

    host = args.host or preset_host
    port = args.port or int(env_first("SMTP_PORT", default=str(preset_port or 0)))
    security = args.security or preset_security or "ssl"

    missing = []
    if not host:
        missing.append("SMTP_HOST or EMAIL_PROVIDER")
    if not port:
        missing.append("SMTP_PORT")
    if not sender:
        missing.append("EMAIL_FROM or EMAIL_USER")
    if not recipients:
        missing.append("EMAIL_TO or --to")
    if not user:
        missing.append("EMAIL_USER")
    if not password:
        missing.append("EMAIL_AUTH_CODE")
    if security not in VALID_SECURITY:
        missing.append("valid SMTP_SECURITY")

    if missing:
        print("Email configuration is incomplete:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        print(
            "Use EMAIL_PROVIDER=qq|163|gmail|outlook, or configure custom SMTP.",
            file=sys.stderr,
        )
        return 2

    body = load_body(args)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = args.subject
    message.set_content(body, charset="utf-8")
    attach_files(message, args.attach)

    safe_summary = (
        f"provider={provider} host={host}:{port} security={security} "
        f"from={sender} to={','.join(recipients)} "
        f"attachments={len(args.attach)}"
    )
    if args.check_config:
        print(f"Email configuration OK: {safe_summary}")
        return 0
    if args.dry_run:
        print(f"Email dry run OK: {safe_summary}")
        print(f"subject={args.subject!r} body_characters={len(body)}")
        return 0

    context = ssl.create_default_context()
    try:
        if security == "ssl":
            with smtplib.SMTP_SSL(
                host, port, timeout=args.timeout, context=context
            ) as smtp:
                smtp.login(user, password)
                smtp.send_message(message, from_addr=sender, to_addrs=recipients)
        else:
            with smtplib.SMTP(host, port, timeout=args.timeout) as smtp:
                smtp.ehlo()
                if security == "starttls":
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(message, from_addr=sender, to_addrs=recipients)
    except (OSError, smtplib.SMTPException) as exc:
        print(
            f"Email send failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"Email sent successfully: {safe_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
