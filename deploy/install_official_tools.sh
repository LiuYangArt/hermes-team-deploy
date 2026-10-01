#!/bin/sh
# Install pinned official Lark and Meegle CLIs and the few skills the bot uses.
# --image downloads into the image. --activate copies skills into HERMES_HOME
# and points Lark at the existing app as the bot, without printing secrets.
set -eu

mode=${1:-}
manifest=${OFFICIAL_TOOLS_MANIFEST:-/opt/hermes/official-tools.json}
if [ ! -f "$manifest" ]; then
  here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  manifest=$here/official-tools.json
fi

python3 - "$manifest" << 'PY' > /tmp/official-tools.env
import json, shlex, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"LARK_CLI={shlex.quote(data['lark_cli'])}")
print(f"MEEGLE_CLI={shlex.quote(data['meegle_cli'])}")
print("LARK_SKILLS=" + shlex.quote(" ".join(data["lark_skills"])))
print("MEEGLE_SKILLS=" + shlex.quote(" ".join(data["meegle_skills"])))
PY
# shellcheck disable=SC1091
. /tmp/official-tools.env
rm -f /tmp/official-tools.env

fetch_skills() {
  dest=$1
  mkdir -p "$dest"
  tmp=$(mktemp -d)
  curl -fsSL "https://github.com/larksuite/cli/archive/refs/tags/v${LARK_CLI}.tar.gz" | tar -xz -C "$tmp"
  curl -fsSL "https://github.com/larksuite/meegle-cli/archive/refs/tags/v${MEEGLE_CLI}.tar.gz" | tar -xz -C "$tmp"
  lark_src=$tmp/cli-$LARK_CLI/skills
  meegle_src=$tmp/meegle-cli-$MEEGLE_CLI/skills
  for name in $LARK_SKILLS; do
    rm -rf "$dest/$name"
    cp -a "$lark_src/$name" "$dest/$name"
  done
  for name in $MEEGLE_SKILLS; do
    rm -rf "$dest/$name"
    cp -a "$meegle_src/$name" "$dest/$name"
  done
  rm -rf "$tmp"
}

install_clis() {
  npm install -g --prefix /usr/local --allow-scripts=@larksuite/cli,@lark-project/meegle \
    "@larksuite/cli@${LARK_CLI}" \
    "@lark-project/meegle@${MEEGLE_CLI}"
}

case "$mode" in
  --image)
    install_clis
    fetch_skills /opt/hermes/official-skills
    ;;
  --activate)
    home=${HERMES_HOME:?Set HERMES_HOME}
    if [ -d /opt/hermes/official-skills/lark-shared ]; then
      mkdir -p "$home/skills"
      for name in $LARK_SKILLS $MEEGLE_SKILLS; do
        rm -rf "$home/skills/$name"
        cp -a "/opt/hermes/official-skills/$name" "$home/skills/$name"
      done
    else
      fetch_skills "$home/skills"
    fi
    if [ -n "${FEISHU_APP_ID:-}" ] && [ -n "${FEISHU_APP_SECRET:-}" ]; then
      # Official bind reads the Hermes env file. Process env alone is not enough.
      python3 - "$home/.env" << 'PY'
import os, sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8") if path.exists() else ""
if text and not text.endswith("\n"):
    text += "\n"
existing = {}
for line in text.splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    existing[key] = value
for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
    value = os.environ.get(key, "")
    if not value or any(char in value for char in "\n\r"):
        raise SystemExit(f"{key} is missing or unsafe to store")
    if existing.get(key) == value:
        continue
    if key in existing:
        lines = []
        for line in text.splitlines():
            lines.append(f"{key}={value}" if line.startswith(key + "=") else line)
        text = "\n".join(lines) + "\n"
    else:
        text += f"{key}={value}\n"
path.write_text(text, encoding="utf-8")
path.chmod(0o600)
PY
      if [ "$(id -u)" = 0 ]; then
        chown hermes:hermes "$home/.env" || true
      fi
      lark-cli config bind --source hermes --identity bot-only >/dev/null
      brand=feishu
      if [ "${FEISHU_DOMAIN:-}" = "lark" ]; then
        brand=lark
      fi
      python3 - "$home/.lark-cli/hermes/config.json" "$brand" << 'PY'
import json, sys
path, brand = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
for app in data.get("apps") or []:
    app["brand"] = brand
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(path, "a", encoding="utf-8").write("\n")
PY
      lark-cli config default-as bot >/dev/null
      lark-cli config strict-mode bot >/dev/null
      # The chat terminal uses a different home. Keep it on the same bot-only config.
      mkdir -p "$home/home"
      if [ -e "$home/home/.lark-cli" ] && [ ! -L "$home/home/.lark-cli" ]; then
        rm -rf "$home/home/.lark-cli"
      fi
      ln -sfn "$home/.lark-cli" "$home/home/.lark-cli"
    fi
    ;;
  *)
    echo "usage: install_official_tools.sh --image|--activate" >&2
    exit 2
    ;;
esac
