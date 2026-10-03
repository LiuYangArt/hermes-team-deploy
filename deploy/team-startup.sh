#!/command/with-contenv sh
# Copy the image's Lark/Meegle tools and team extensions into this home.
# Personal grants stay in the home and are not replaced.
set -eu

home=${HERMES_HOME:-/opt/data}
src=${HERMES_TEAM_EXTENSIONS:-/opt/hermes/team-extensions}
tools=${HERMES_OFFICIAL_TOOLS_SCRIPT:-/opt/hermes/install_official_tools.sh}
enable=${HERMES_ENABLE_PLUGINS:-/opt/hermes/enable_team_plugins.py}

mkdir -p "$home/plugins"
for name in lark-topics lark-access personal-auth; do
  if [ ! -f "$src/$name/plugin.yaml" ]; then
    echo "missing team extension: $name" >&2
    exit 1
  fi
  rm -rf "$home/plugins/$name"
  cp -a "$src/$name" "$home/plugins/$name"
  rm -rf "$home/plugins/$name/__pycache__"
done

if [ ! -f "$home/config.yaml" ]; then
  printf 'plugins:\n  enabled: []\n' > "$home/config.yaml"
fi
python3 "$enable" "$home/config.yaml"

if [ -f "$tools" ]; then
  HERMES_HOME="$home" sh "$tools" --activate
fi

if [ "$(id -u)" = 0 ]; then
  chown -R hermes:hermes "$home/plugins"
  if [ -d "$home/skills" ]; then
    chown -R hermes:hermes "$home/skills"
  fi
  chown hermes:hermes "$home/config.yaml" 2>/dev/null || true
fi
