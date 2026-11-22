#!/bin/bash
# QTS follower apply — runs ON a fleet node via ssh stdin.
# Args: <conduit-ip> <follower|master>
# Backs up /etc/systemd/timesyncd.conf (timestamped), points timesyncd at
# the conduit with the network pool as fallback, restarts timesyncd, and
# prints before/after evidence. Slew-only: this script never sets the
# clock; timesyncd itself only slews.
set -u
CONDUIT="$1"
MODE="${2:-follower}"
if ! sudo -n true 2>/dev/null; then
  echo "RESULT=NO_SUDO"
  exit 0
fi
echo "--- before (active [Time] settings):"
grep -E "^(NTP|FallbackNTP|PollInterval|RootDistance|ConnectionRetry)" /etc/systemd/timesyncd.conf || echo "(none)"
TS=$(date +%Y%m%dT%H%M%SZ)
if ! sudo cp /etc/systemd/timesyncd.conf "/etc/systemd/timesyncd.conf.qts-bak-$TS"; then
  echo "RESULT=BACKUP_FAILED"
  exit 0
fi
echo "backup=/etc/systemd/timesyncd.conf.qts-bak-$TS"
{
  echo "# QTS - Quantum Time Sync apply 2026-11-22 (task 26.6.11/8.7.14/8.6.10)"
  echo "[Time]"
  echo "NTP=$CONDUIT"
  echo "FallbackNTP=ntp.ubuntu.com"
  if [ "$MODE" = "master" ]; then
    echo "PollIntervalMinSec=32"
    echo "PollIntervalMaxSec=1024"
  fi
} | sudo tee /etc/systemd/timesyncd.conf >/dev/null
if ! sudo systemctl restart systemd-timesyncd; then
  echo "RESULT=RESTART_FAILED"
  exit 0
fi
sleep 12
echo "--- after:"
timedatectl 2>/dev/null | grep "System clock synchronized" || echo "sync-state-unavailable"
timedatectl timesync-status 2>/dev/null | grep -E "Server:|Offset:|Jitter:|PollInterval" || echo "timesync-status-not-ready"
echo "RESULT=DONE"
