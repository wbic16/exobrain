#!/usr/bin/env python3
"""Quantum Time Sync — echolocation probe (dolphin rule a).

Runs ON a surveyor. Emits NTP timestamp exchanges — pings, in the
dolphin sense — at its subset members and its fellow surveyors, and
reads the echoes: each exchange returns the target's own timestamps,
from which the offset of the target relative to the caller and the
round-trip delay are computed. The delay bounds the offset error
(bound = delay/2, the Cristian bound): a surveyor never claims to
know a subset member's time more precisely than the echo allows.

This replaces the collector-VM sampling of sample.py as the standing
instrument: the probes originate on disciplined fleet clocks (the
surveyors), not on an undisciplined VM, and they ride the NTP
protocol itself — no ssh inside the measured interval.

One exchange (NTP client mode, UDP/123):
  t1  caller clock, request sent
  t2  target clock, request received     (echo's own stamp)
  t3  target clock, reply sent           (echo's own stamp)
  t4  caller clock, reply received
  offset(target - caller) = ((t2 - t1) + (t3 - t4)) / 2
  delay                   = (t4 - t1) - (t3 - t2)

Several exchanges per target; the minimum-delay one is kept (the
least queueing-contaminated echo, the house min-delay discipline).

Every emitted atom carries the signature whistle (dolphin rule c):
caller=<node> and hw=<handwriting id> — the caller's timing identity
derived from its latest census atom (see hw_id in triangulate.py).
A flow names its caller, by its timing character, not just its
address.

Read-only: an NTP query changes nothing on the target. Stdlib only.

Usage (on a surveyor):
  python3 probe.py --caller ni-knight --hw ni-knight:A+/Q \
      --targets ashfall-haven:100.102.119.123,elven-path:100.120.211.72 \
      [--rounds 4] [--timeout 2.0]
"""
import argparse
import socket
import struct
import sys
import time

NTP_EPOCH_DELTA = 2208988800  # seconds, 1900 -> 1970


def ntp_to_unix(sec, frac):
    return (sec - NTP_EPOCH_DELTA) + frac / 4294967296.0


def exchange(ip, timeout):
    """One NTP Cristian exchange. Returns (offset_ms, delay_ms,
    stratum) or None."""
    pkt = bytearray(48)
    pkt[0] = 0x23  # LI=0, VN=4, Mode=3 (client)
    t1 = time.time()
    # transmit timestamp = t1, so the target can (and chrony does)
    # sanity-check the originate timestamp on the way back.
    sec = int(t1) + NTP_EPOCH_DELTA
    frac = int((t1 - int(t1)) * 4294967296)
    struct.pack_into(">II", pkt, 40, sec, frac)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(bytes(pkt), (ip, 123))
        data, _ = sock.recvfrom(512)
    except (socket.timeout, OSError):
        return None
    finally:
        sock.close()
    t4 = time.time()
    if len(data) < 48:
        return None
    stratum = data[1]
    r_sec, r_frac = struct.unpack_from(">II", data, 32)
    x_sec, x_frac = struct.unpack_from(">II", data, 40)
    t2 = ntp_to_unix(r_sec, r_frac)
    t3 = ntp_to_unix(x_sec, x_frac)
    offset_ms = ((t2 - t1) + (t3 - t4)) / 2 * 1000.0
    delay_ms = ((t4 - t1) - (t3 - t2)) * 1000.0
    return offset_ms, delay_ms, stratum


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--caller", required=True)
    ap.add_argument("--hw", required=True)
    ap.add_argument("--targets", required=True,
                    help="comma-separated name:ip pairs")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=2.0)
    args = ap.parse_args()

    for pair in args.targets.split(","):
        name, ip = pair.split(":")
        best = None
        for r in range(args.rounds):
            if r:
                time.sleep(0.3)
            got = exchange(ip, args.timeout)
            if got and (best is None or got[1] < best[1]):
                best = got
        if best is None:
            print(f"echo caller={args.caller} hw={args.hw} "
                  f"target={name} ip={ip} ok=no")
            continue
        off, delay, stratum = best
        print(f"echo caller={args.caller} hw={args.hw} "
              f"target={name} ip={ip} ok=yes "
              f"offset_ms={off:.3f} delay_ms={delay:.3f} "
              f"bound_ms={delay / 2:.3f} stratum={stratum} "
              f"rounds={args.rounds} method=ntp-cristian-min-delay")
    return 0


if __name__ == "__main__":
    sys.exit(main())
