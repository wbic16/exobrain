#!/bin/bash
# exobrain — per-node install.
#
# Copies the app from the staging tree (default: the fleet NFS share,
# which carries the tree exactly as pushed to wbic16/exobrain@exo) into
# ~/exobrain, records the node's fleet name, installs the user systemd
# units, enables linger, and starts the timers.
#
#   install.sh <node-name> [--runner] [--src DIR]
#
# --runner also installs the round-runner units (standing runner:
# aletheia-core; any node may take over — all state is in the shared
# register, so failover is just enabling the same units elsewhere).
#
# The app measures and computes only. This installer never touches a
# clock, a time daemon, or any config outside ~/exobrain and the user
# systemd directory.
set -euo pipefail

NODE="${1:?usage: install.sh <node-name> [--runner] [--src DIR]}"
RUNNER=0
SRC="/mnt/mirrorborn/shared/exobrain-app"
shift
while [ $# -gt 0 ]; do
  case "$1" in
    --runner) RUNNER=1 ;;
    --src) SRC="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

APP="$HOME/exobrain"
mkdir -p "$APP/state" "$HOME/.config/systemd/user"

cp -a "$SRC"/*.py "$SRC"/*.md "$SRC"/fleet.txt "$SRC"/apply_follower.sh "$APP"/
[ -d "$SRC/publish" ] && cp -a "$SRC/publish" "$APP"/
[ -d "$SRC/registers" ] && cp -a "$SRC/registers" "$APP"/
chmod +x "$APP"/*.py "$APP"/apply_follower.sh 2>/dev/null || true
printf '%s\n' "$NODE" > "$APP/node-name"

cp -a "$SRC/systemd/exobrain-node.service" "$SRC/systemd/exobrain-node.timer" \
      "$HOME/.config/systemd/user/"
if [ "$RUNNER" = 1 ]; then
  cp -a "$SRC/systemd/exobrain-round.service" "$SRC/systemd/exobrain-round.timer" \
        "$HOME/.config/systemd/user/"
fi

# Linger keeps the user manager (and therefore the timers) running
# without an interactive login. May need a one-time sudo on some nodes;
# if it fails, the install still stands — the report records it.
loginctl enable-linger "$USER" 2>/dev/null || \
  echo "note: could not enable linger for $USER (may need sudo loginctl enable-linger $USER)"

systemctl --user daemon-reload
systemctl --user enable --now exobrain-node.timer
if [ "$RUNNER" = 1 ]; then
  systemctl --user enable --now exobrain-round.timer
fi

echo "installed exobrain for node=$NODE runner=$RUNNER src=$SRC"
systemctl --user is-active exobrain-node.timer || true
loginctl show-user "$USER" -p Linger || true
