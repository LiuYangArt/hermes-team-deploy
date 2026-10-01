#!/bin/sh
# Build from this repository alone. The pinned program is downloaded from the
# public commit recorded in deploy/.env. Team changes in this repository are
# applied afterward.
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project"

if [ ! -f deploy/.env ]; then
  echo '先把 deploy/.env.example 复制为 deploy/.env，并写上状态目录和用户编号。' >&2
  exit 1
fi

read_setting() {
  python3 -c '
from pathlib import Path
import sys
key = sys.argv[1]
vals = {}
for line in Path("deploy/.env").read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    name, value = stripped.split("=", 1)
    vals[name.strip()] = value.strip()
print(vals.get(key, ""))
' "$1"
}

revision=$(read_setting HERMES_CORE_REVISION)
repository=$(read_setting HERMES_CORE_REPOSITORY)
if [ -z "$repository" ]; then
  repository=https://github.com/NousResearch/hermes-agent.git
fi
printf '%s\n' "$revision" | grep -Eq '^[0-9a-f]{40}$' || {
  echo 'deploy/.env 里的程序版本号不正确。请保持 deploy/.env.example 里的那一行。' >&2
  exit 1
}

dest=.build/hermes-core
mkdir -p .build
if [ ! -d "$dest/.git" ]; then
  rm -rf "$dest"
  git init -q "$dest"
  git -C "$dest" remote add origin "$repository"
else
  git -C "$dest" remote set-url origin "$repository"
fi
if ! git -C "$dest" -c http.version=HTTP/1.1 fetch --depth 1 origin "$revision"; then
  echo '下载钉住的程序版本失败。请确认这台机器能访问 GitHub，且没有改过版本号。' >&2
  exit 1
fi
git -C "$dest" checkout -q --detach --force FETCH_HEAD
actual=$(git -C "$dest" rev-parse HEAD)
[ "$actual" = "$revision" ] || {
  echo '下载到的程序版本和记录不一致。' >&2
  exit 1
}

docker build --label "org.opencontainers.image.revision=$revision" -t "hermes-team-core:$revision" "$dest"
docker compose --env-file deploy/.env -f deploy/compose.yaml build
