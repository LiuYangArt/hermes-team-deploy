"""Add the team plugins to a Hermes config without disturbing other settings."""

from __future__ import annotations

import sys
from pathlib import Path

TEAM_PLUGINS = ("lark-topics", "lark-access", "personal-auth")


def ensure_team_plugins(text: str, names: tuple[str, ...] = TEAM_PLUGINS) -> str:
    lines = text.splitlines()
    plugins_at = _top_level_key(lines, "plugins:")
    if plugins_at is None:
        block = ["plugins:", "  enabled:", *[f"    - {name}" for name in names]]
        body = text
        if body and not body.endswith("\n"):
            body += "\n"
        return body + "\n".join(block) + "\n"

    end = _section_end(lines, plugins_at)
    section = lines[plugins_at + 1:end]
    enabled_rel = _child_key(section, "enabled:")
    if enabled_rel is None:
        insert = ["  enabled:", *[f"    - {name}" for name in names]]
        lines[plugins_at + 1:plugins_at + 1] = insert
        return _join(lines)

    enabled_line = section[enabled_rel]
    inline = enabled_line.split("#", 1)[0].split(":", 1)[1].strip()
    if inline:
        present = _flow_names(inline)
        if present is None:
            raise ValueError("plugins.enabled must be a list")
        merged = list(dict.fromkeys([*present, *names]))
        indent = _indent(enabled_line)
        replacement = [" " * indent + "enabled:", *[" " * (indent + 2) + f"- {name}" for name in merged]]
        abs_at = plugins_at + 1 + enabled_rel
        lines[abs_at:abs_at + 1] = replacement
        return _join(lines)

    present, items_end = _block_items(section, enabled_rel)
    missing = [name for name in names if name not in present]
    if missing:
        indent = _indent(enabled_line) + 2
        added = [" " * indent + f"- {name}" for name in missing]
        abs_end = plugins_at + 1 + items_end
        lines[abs_end:abs_end] = added
    return _join(lines)


def _top_level_key(lines: list[str], key: str) -> int | None:
    for index, line in enumerate(lines):
        if line.startswith("#") or not line.strip():
            continue
        if _indent(line) == 0 and line.split("#", 1)[0].strip() == key:
            return index
    return None


def _section_end(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if _indent(raw) == 0:
            return index
    return len(lines)


def _child_key(section: list[str], key: str) -> int | None:
    for index, raw in enumerate(section):
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        head = raw.split("#", 1)[0].strip()
        if head == key or head.startswith(key):
            return index
    return None


def _block_items(section: list[str], enabled_rel: int) -> tuple[list[str], int]:
    base = _indent(section[enabled_rel])
    present: list[str] = []
    index = enabled_rel + 1
    while index < len(section):
        raw = section[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        if _indent(raw) <= base:
            break
        body = raw.strip()
        if body.startswith("- "):
            present.append(body[2:].strip().strip("'\""))
        index += 1
    return present, index


def _flow_names(value: str) -> list[str] | None:
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _join(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: enable_team_plugins.py CONFIG", file=sys.stderr)
        return 2
    path = Path(argv[1])
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(ensure_team_plugins(text), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
