#!/usr/bin/env python3
"""Look over a new install before it starts. Do not print secret values."""

from __future__ import annotations

import argparse
import json
import socket
import stat
import sys
from pathlib import Path

REQUIRED = ("FEISHU_APP_ID", "FEISHU_APP_SECRET")
_BLANK_MARKERS = ("changeme", "example", "xxx", "your-", "placeholder", "todo")


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_blank(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _BLANK_MARKERS)


def bot_problems(path: Path) -> tuple[list[str], str]:
    if not path.is_file():
        return ["还没有机器人凭据文件"], ""
    problems: list[str] = []
    mode = path.stat().st_mode
    if mode & (stat.S_IROTH | stat.S_IWOTH):
        problems.append("机器人凭据文件其他人也能读，请改成只有本机管理员可读")
    data = parse_env(path.read_text(encoding="utf-8"))
    if any(is_blank(data.get(key, "")) for key in REQUIRED):
        problems.append("机器人凭据还没填完整")
    domain = data.get("FEISHU_DOMAIN", "").strip().lower()
    if domain not in {"lark", "feishu"}:
        problems.append("需要写明用的是 Lark 还是飞书")
    connection = data.get("FEISHU_CONNECTION_MODE", "websocket").strip().lower() or "websocket"
    if connection != "websocket":
        problems.append("云服务器这套只用长连接，不开放入站端口")
    return problems, domain


def access_problems(path: Path) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    if not path.is_file():
        notes.append("还没有管理员名单。进门开关会按默认开着，谁第一次说话都能进来，但没有管理员")
        return [], notes
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["管理员名单文件不是有效的配置"], notes
    if not isinstance(data, dict):
        return ["管理员名单文件格式不对"], notes
    problems: list[str] = []
    if "auto_enroll" in data and not isinstance(data["auto_enroll"], bool):
        problems.append("进门开关只能是开或关")
    admins = data.get("admins", [])
    if not isinstance(admins, list) or not all(isinstance(item, str) for item in admins):
        problems.append("管理员名单格式不对")
        return problems, notes
    if any(is_blank(item) for item in admins):
        problems.append("管理员名单还是示例，请换成真人的标识")
    elif not admins:
        notes.append("管理员名单是空的，没有人能改技能、人设和定时任务")
    return problems, notes


def layout_problems(state: Path, repo: Path) -> list[str]:
    if not state.is_dir():
        return ["状态目录还不存在"]
    problems: list[str] = []
    try:
        state.resolve().relative_to(repo.resolve())
    except ValueError:
        pass
    else:
        problems.append("状态目录不能放在代码仓库里面")
    if not (state / "data").is_dir() or not (state / "workspace").is_dir():
        problems.append("状态目录里还缺数据和会话两个文件夹")
    config = state / "data" / "config.yaml"
    if not config.is_file() or config.stat().st_size == 0:
        problems.append("还缺模型配置。请用官方空白模板填写，不要另写一套")
    if state.stat().st_mode & stat.S_IWOTH:
        problems.append("状态目录其他人也能改")
    return problems


def compose_problems(text: str) -> list[str]:
    for line in text.splitlines():
        if line.strip().startswith("ports:"):
            return ["这套安装不应向宿主机开放端口"]
    return []


def image_problems(dockerfile: str) -> list[str]:
    if "jobs/" in dockerfile or "COPY jobs" in dockerfile:
        return ["镜像构建不应带上任务包"]
    return []


def network_problem(domain: str, connect) -> list[str]:
    if domain not in {"lark", "feishu"}:
        return []
    host = "open.larksuite.com" if domain == "lark" else "open.feishu.cn"
    try:
        connect(host, 443)
    except OSError:
        return [f"连不上 {host}，请先确认这台机器能访问外网"]
    return []


def report(problems: list[str], notes: list[str]) -> str:
    lines = [f"要先处理：{item}" for item in problems]
    lines.extend(f"请知道：{item}" for item in notes)
    if problems:
        lines.append("还不能启动。")
    else:
        lines.append("安装前检查通过。")
    return "\n".join(lines)


def run_checks(state: Path, repo: Path, compose: str, dockerfile: str, connect) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    bot, domain = bot_problems(state / "bot.env")
    problems.extend(bot)
    access, access_notes = access_problems(state / "data" / "lark-access" / "config.json")
    problems.extend(access)
    notes.extend(access_notes)
    problems.extend(layout_problems(state, repo))
    problems.extend(compose_problems(compose))
    problems.extend(image_problems(dockerfile))
    problems.extend(network_problem(domain, connect))
    return problems, notes


def default_connect(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=5):
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装前检查")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--dockerfile", type=Path, required=True)
    parser.add_argument("--skip-network", action="store_true")
    args = parser.parse_args(argv)
    connect = (lambda host, port: None) if args.skip_network else default_connect
    problems, notes = run_checks(
        args.state,
        args.repo,
        args.compose.read_text(encoding="utf-8"),
        args.dockerfile.read_text(encoding="utf-8"),
        connect,
    )
    print(report(problems, notes))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
