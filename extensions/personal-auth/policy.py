"""Bind Lark task reads and Meegle to the person who is speaking.

A shared login on this machine would let the next person act as the previous
one. Missing grants stop the call. They are never replaced with someone else's
files, the bot identity, or the retired shared Meegle directory.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

REFUSAL_UNKNOWN = "看不出是谁在说话，所以不能查任务，也不能操作 Meegle。"
REFUSAL_LARK = "还没有你自己的任务授权。请用 personal_auth(service=lark, action=start) 给本人发起授权；不会改用别人的登录。"
REFUSAL_MEEGLE = "还没有你自己的 Meegle 授权。请用 personal_auth(service=meegle, action=start)；不会改用别人的登录。"
REFUSAL_FOREIGN = "不能读取、指定或改用别人的授权。"
REFUSAL_MIXED = "查自己的任务和修改任务要分开做。修改任务仍然用机器人身份。"
REFUSAL_BOT_READ = "查自己的任务不能改成机器人身份。"
REFUSAL_SCRIPT = "查自己的任务和 Meegle 要直接运行对应命令，这样才会用到你自己的授权。"

TURN_NOTE = (
    "查这个人自己的 Lark 任务，以及 Meegle 的查看、待办、创建和修改，只能用这个人自己的授权。"
    "普通成员可用 personal_auth 工具为本人扫码授权、查看状态或退出，不需要管理员。"
    "缺少授权时调用 start，展示原样链接、二维码和有效期；本轮先结束。本人说已授权后调用 complete，再继续原请求。"
    "设备码由工具保管，不运行 CLI auth login/logout，不手动读写授权目录。Meegle 首次授权先完成 Lark 身份核对。"
    "Lark 任务的新建、修改、完成、重新打开、分配负责人和加备注仍然用机器人身份。"
)

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
_SENSITIVE = re.compile(
    r"personal-auth|\.meegle|credentials\.enc|MEEGLE_USER_ACCESS_TOKEN|"
    r"LARKSUITE_CLI_|MEEGLE_|\.lark-cli|\.local/share/lark-cli",
    re.IGNORECASE,
)
_HOME_ASSIGN = re.compile(r"(?:^|[\s;&|`(])HOME\s*=", re.IGNORECASE)
_OWN_TASK = re.compile(r"\+(?:get-my-tasks|get-related-tasks)\b")
_BOT_TASK = re.compile(r"\+(?:create|update|complete|reopen|assign|comment)\b")
_MEEGLE = re.compile(r"(?:^|[^A-Za-z0-9_-])meegle(?:$|[^A-Za-z0-9_-])", re.IGNORECASE)
_AS_BOT = re.compile(r"--as(?:\s+|=)bot\b")


def safe_id(value: str) -> bool:
    text = str(value or "")
    return bool(_ID.match(text)) and not text.startswith("_")


def canonical(user_ids: tuple[str, ...], links: dict[str, str]) -> str:
    ids = [item for item in user_ids if safe_id(item)]
    if not ids:
        return ""
    mapped = [links.get(item, item) for item in ids]
    for item in mapped:
        if item.startswith("ou_") and safe_id(item):
            return item
    return mapped[0] if safe_id(mapped[0]) else ""


def observe(links: dict[str, str], user_ids: tuple[str, ...]) -> dict[str, str]:
    """Ids seen on one message belong to one person. The open id names the directory."""
    clean = [item for item in user_ids if safe_id(item)]
    if not clean:
        return links
    group = set(clean)
    changed = True
    while changed:
        changed = False
        for key, value in links.items():
            if key in group or value in group:
                for item in (key, value):
                    if item not in group and safe_id(item):
                        group.add(item)
                        changed = True
    preferred = [item for item in group if item.startswith("ou_")]
    target = preferred[0] if preferred else canonical(clean, links)
    if not target:
        return links
    updated = dict(links)
    for item in group:
        updated[item] = target
    return updated


def load_links(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("ids") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if safe_id(key) and safe_id(value)
    }


def save_links(path: Path, links: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ids": links}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def meegle_home(root: Path, speaker: str) -> Path:
    return root / "personal-auth" / speaker / "meegle-home"


def lark_config_dir(root: Path, speaker: str) -> Path:
    return root / "personal-auth" / speaker / "lark"


def bot_lark_dir(root: Path) -> Path:
    return root / ".lark-cli"


def has_meegle_grant(root: Path, speaker: str) -> bool:
    folder = meegle_home(root, speaker)
    return _verified(folder, speaker) and (folder / ".meegle" / "credentials.enc").is_file()


def _verified(folder: Path, speaker: str) -> bool:
    try:
        return json.loads((folder / "verified.json").read_text(encoding="utf-8")).get("speaker") == speaker
    except (OSError, ValueError, AttributeError):
        return False


def has_lark_grant(root: Path, speaker: str) -> bool:
    if not _verified(lark_config_dir(root, speaker), speaker):
        return False
    file = lark_config_dir(root, speaker) / "hermes" / "config.json"
    if not file.is_file():
        return False
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    apps = data.get("apps") if isinstance(data, dict) else None
    if not isinstance(apps, list):
        return False
    for app in apps:
        users = app.get("users") if isinstance(app, dict) else None
        if not isinstance(users, list):
            continue
        for user in users:
            if isinstance(user, dict) and user.get("userOpenId") == speaker:
                return True
    return False


def retire_shared_meegle(root: Path) -> str:
    """Move the machine-wide Meegle login out of the default home.

    It is not attached to any person. A later request must not find it there.
    """
    source = root / "home" / ".meegle"
    if not source.exists() and not source.is_symlink():
        return "absent"
    retired = root / "personal-auth" / "_retired-shared"
    retired.mkdir(parents=True, exist_ok=True)
    retired.chmod(0o700)
    dest = retired / "meegle"
    number = 1
    while dest.exists() or dest.is_symlink():
        number += 1
        dest = retired / f"meegle-{number}"
    source.rename(dest)
    return "moved"


def tool_decision(
    *,
    user_ids: tuple[str, ...],
    tool_name: str,
    args: dict,
    root: Path,
    links: dict[str, str],
) -> dict | None:
    name = str(tool_name or "")
    if name in {"read_file", "write_file", "patch"}:
        return _block(REFUSAL_FOREIGN) if _sensitive_text(str(args.get("path") or "")) else None
    if name == "execute_code":
        code = str(args.get("code") or "")
        if _sensitive_text(code):
            return _block(REFUSAL_FOREIGN)
        if "lark-cli" in code or _uses_meegle(code):
            return _block(REFUSAL_SCRIPT)
        return None
    if name != "terminal":
        return None
    command = str(args.get("command") or "")
    if not command.strip():
        return None
    if _sensitive_text(command):
        return _block(REFUSAL_FOREIGN)
    own = _owns_tasks(command)
    meegle = _uses_meegle(command)
    lark = "lark-cli" in command
    bot_task = bool(_BOT_TASK.search(command))
    if not lark and not meegle:
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return _block(REFUSAL_SCRIPT)
    # Only one direct CLI invocation can receive a member's credentials.
    if not words or words[0] not in {"lark-cli", "meegle", "/usr/local/bin/lark-cli", "/usr/local/bin/meegle"} or any(word and all(c in ";&|<>()" for c in word) for word in words):
        return _block(REFUSAL_SCRIPT)
    lark = Path(words[0]).name == "lark-cli"
    meegle = not lark
    if "auth" in words and words[1:3] != ["auth", "qrcode"]:
        return _block("管理自己的授权请使用 personal_auth 工具，普通成员也可使用。")
    if meegle and any(word in {"config", "profile"} for word in words[1:3]):
        return _block("个人 Meegle 配置由授权工具管理，不能在普通命令中修改。")
    if any(word == "--profile" or word.startswith("--profile=") for word in words):
        return _block(REFUSAL_FOREIGN)
    if own and bot_task:
        return _block(REFUSAL_MIXED)
    if own and _AS_BOT.search(command):
        return _block(REFUSAL_BOT_READ)
    if _HOME_ASSIGN.search(command):
        return _block(REFUSAL_FOREIGN)
    speaker = canonical(user_ids, links)
    if not speaker:
        return _block(REFUSAL_UNKNOWN)
    env: dict[str, str] = {}
    ensure: list[str] = []
    if meegle:
        if not has_meegle_grant(root, speaker):
            return _block(REFUSAL_MEEGLE)
        home = meegle_home(root, speaker)
        env["HOME"] = home.as_posix()
        env["USER"] = "hermes"
        ensure.append(home.as_posix())
        if bot_task:
            env["LARKSUITE_CLI_CONFIG_DIR"] = bot_lark_dir(root).as_posix()
    if own:
        if not has_lark_grant(root, speaker):
            return _block(REFUSAL_LARK)
        env["LARKSUITE_CLI_CONFIG_DIR"] = lark_config_dir(root, speaker).as_posix()
        env["LARKSUITE_CLI_DATA_DIR"] = (lark_config_dir(root, speaker) / ".data").as_posix()
    elif lark:
        env["LARKSUITE_CLI_CONFIG_DIR"] = bot_lark_dir(root).as_posix()
        env["LARKSUITE_CLI_DATA_DIR"] = (root / ".local" / "share").as_posix()
    if not env:
        return None
    return {
        "action": "modify",
        "args": {"command": _wrap(shlex.join(words), env)},
        "ensure_dirs": ensure,
    }


def _sensitive_text(value: str) -> bool:
    return bool(_SENSITIVE.search(value))


def _owns_tasks(command: str) -> bool:
    return bool(_OWN_TASK.search(command))


def _uses_meegle(command: str) -> bool:
    return bool(_MEEGLE.search(command))


def _wrap(command: str, env: dict[str, str]) -> str:
    parts = [f"{key}={shlex.quote(value)}" for key, value in env.items()]
    # The command was already parsed and restricted to one direct CLI call above.
    # Keep that argv visible to the platform safety checker; wrapping it in
    # `sh -c` makes a read-only personal query look like arbitrary shell code.
    return "env " + " ".join(parts) + " " + command


def _block(message: str) -> dict:
    return {"action": "block", "message": message}
