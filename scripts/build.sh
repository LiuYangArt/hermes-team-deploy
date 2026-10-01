#!/bin/sh
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project"
# Compose owns path parsing; no shell evaluation of the local settings file.
revision=$(python3 -c 'from pathlib import Path; print(next(x.split("=",1)[1].strip() for x in Path("deploy/.env").read_text().splitlines() if x.startswith("HERMES_CORE_REVISION=")))')
actual=$(git -C ../hermes-team rev-parse HEAD)
[ "$revision" = "$actual" ] || { echo 'Core revision differs from deploy/.env; update it before building.' >&2; exit 1; }
[ -z "$(git -C ../hermes-team status --porcelain)" ] || { echo 'Commit Core changes before building a revision-tagged image.' >&2; exit 1; }
docker build --label "org.opencontainers.image.revision=$revision" -t "hermes-team-core:$revision" ../hermes-team
docker compose --env-file deploy/.env -f deploy/compose.yaml build
