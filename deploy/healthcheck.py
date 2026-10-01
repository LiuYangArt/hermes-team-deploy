#!/usr/bin/env python3
"""See whether the running bot is up, and hide secrets before showing logs."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from preflight import parse_env

_ASSIGNED = re.compile(
    r"(?i)\b(FEISHU_APP_ID|FEISHU_APP_SECRET|FEISHU_ENCRYPT_KEY|FEISHU_VERIFICATION_TOKEN|"
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|API_KEY|APP_SECRET)\b(\s*[=:]\s*)(\S+)"
)
_JSON_SECRET = re.compile(
    r'(?i)("(?:app_secret|app_id|token|access_token|refresh_token|api_key)"\s*:\s*")([^"]+)(")'
)
_QUERY_SECRET = re.compile(r"(?i)(access_key|ticket|app_secret|secret)=([^&\s]+)")


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if len(secret) >= 8:
            text = text.replace(secret, "[已隐藏]")
    text = _ASSIGNED.sub(r"\1\2[已隐藏]", text)
    text = _JSON_SECRET.sub(r"\1[已隐藏]\3", text)
    text = _QUERY_SECRET.sub(r"\1=[已隐藏]", text)
    return text


def last_connect(lowered: str) -> int:
    latest = lowered.rfind("websocket connected")
    needle = "connected to wss://"
    start = 0
    while True:
        found = lowered.find(needle, start)
        if found < 0:
            return latest
        if lowered[max(0, found - 3):found] != "dis":
            latest = found
        start = found + len(needle)


def assess(running: bool, log: str) -> tuple[bool, str]:
    if not running:
        return False, "容器没有在运行"
    lowered = log.lower()
    error_at = lowered.rfind("traceback (most recent call last)")
    connected_at = last_connect(lowered)
    disconnected_at = lowered.rfind("disconnected to wss://")
    failed_at = lowered.rfind("connect failed")
    if error_at > connected_at:
        return False, "运行日志里有错误，机器人现在不可用"
    if connected_at >= 0 and connected_at > disconnected_at and connected_at > failed_at:
        return True, "容器在运行，并且已经连上"
    if disconnected_at > connected_at or failed_at > connected_at:
        return False, "容器在运行，但和 Lark 的连接断了"
    return False, "容器在运行，但日志里还看不出已经连上"


def status_lines(log: str) -> str:
    markers = (
        "connected to wss://",
        "disconnected to wss://",
        "connect failed",
        "websocket connected",
        "traceback (most recent call last)",
        "hermes gateway starting",
    )
    picked = [line for line in log.splitlines() if any(marker in line.lower() for marker in markers)]
    return "\n".join(picked[-8:])


def load_secrets(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    data = parse_env(path.read_text(encoding="utf-8"))
    return tuple(value for key, value in data.items() if "SECRET" in key or key.endswith("_KEY") or key.endswith("_TOKEN"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查看机器人是否在运行")
    parser.add_argument("--container", default="hermes-team-new")
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(argv)
    running_probe = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", args.container],
        check=False,
        capture_output=True,
        text=True,
    )
    running = running_probe.returncode == 0 and running_probe.stdout.strip() == "true"
    logs = subprocess.run(
        ["docker", "logs", "--tail", "2000", args.container],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log = redact(logs.stdout, load_secrets(args.state / "bot.env"))
    ok, summary = assess(running, log)
    print(summary)
    shown = status_lines(log)
    if shown:
        print(shown)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
