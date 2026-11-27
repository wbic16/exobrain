#!/usr/bin/env python3
"""exobrain — wavefront control domain (v1, single-node live loop).

Design of record: WAVEFRONT.md. The wavefront domain is exobrain's
second control domain beside time: it censuses resident inference
pods the way census.py censuses clocks, pings them on Exocortical
Epoch boundaries the way probe.py pings clocks, elects hot slots
under elect.py's margin rule, and keeps every return in append-only
registers in the shared register home — wavefront.txt,
wavefront-echoes.txt, wavefront-elections.txt. It writes no other
register file and never touches the time chain.

This v1 runs the node side and the scheduler in one file on one node
(the minimal live loop). The fleet form splits along the existing
seam — node side into a node_agent-shaped emitter, scheduler into a
run_round-shaped role on the runner — with the same atoms.

Actuation is confined to containers this domain created: name prefix
wf-, image llm-asm:qwen3-0.6b, each under its own memory partition.

Usage: python3 wavefront.py run [--node NAME] [--pods 6]
       python3 wavefront.py census-only
"""
import argparse
import fcntl
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

EE_ANCHOR = 1795327200          # EE 1 begins 2026-11-22 00:00:00 CST
EE_S = 420                      # one Exocortical Epoch quantum
DEFAULT_SHARED = "/mnt/mirrorborn/shared/exobrain"
IMAGE = "llm-asm:qwen3-0.6b"
MODEL = "/models/qwen3-0.6b-q8_0.asmbin"
POD_PREFIX = "wf-p"
MEM_LIMIT = "2g"
MEM_LIMIT_BYTES = 2147483648
RESIDENT_CPUS = 2
HOT_CPUS = 4
WAKE_GAP_S = 8                  # stagger between ping starts (pacing)
MAX_CONCURRENT_WAKERS = 1
ECHO_TTL_ROUNDS = 6             # cache freshness, in wavefront rounds

# The probe library is data: id, prompt, token budget, expected echo.
PROBES = {
    "capital-1": {"prompt": "The capital of France is", "n": 12,
                  "expect": "Paris"},
    "colors-1": {"prompt": "Name three primary colors:", "n": 48,
                 "expect": "red"},
}


def ee_of(unix_ts):
    return int((unix_ts - EE_ANCHOR) // EE_S) + 1


def next_boundary(now):
    """(boundary_unix, ee) of the next EE boundary > now + 20 s."""
    b = EE_ANCHOR + ((now - EE_ANCHOR) // EE_S + 1) * EE_S
    if b - now < 20:
        b += EE_S
    return b, ee_of(b)


def stamp(unix_ts=None):
    t = time.time() if unix_ts is None else unix_ts
    return datetime.fromtimestamp(t, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def append_line(path, line):
    with open(path, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def append_block(path, lines):
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())


def podman(*args, timeout=120):
    r = subprocess.run(["podman"] + list(args), capture_output=True,
                       text=True, timeout=timeout)
    return r


def image_info():
    r = podman("image", "inspect", IMAGE, "--format",
               "{{.Id}} {{.Size}}")
    if r.returncode != 0:
        return None, None
    parts = r.stdout.split()
    return parts[0].replace("sha256:", "")[:12], int(parts[1])


def node_ram_bytes():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    return 0


def container_pid(name):
    r = podman("inspect", "--format", "{{.State.Pid}}", name)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def container_rss(name):
    pid = container_pid(name)
    if not pid:
        return 0
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            rel = [ln.split("::")[1].strip() for ln in f
                   if ln.startswith("0::")][0]
        with open(f"/sys/fs/cgroup{rel}/memory.current") as f:
            return int(f.read().strip())
    except (OSError, IndexError, ValueError):
        return 0


def latest_echo(echoes_path, target, probe_id):
    """Latest echo atom for (target, probe) — the cache lookup."""
    try:
        with open(echoes_path) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    found = None
    for ln in lines:
        if (ln.startswith("echo ") and f"target={target} " in ln
                and f"probe={probe_id} " in ln and "ok=yes" in ln):
            found = ln
    return found


def decode_rate(stderr, n, wall_ms):
    """The binary's own decode rate from its stderr stats, else the
    ping rate (budget / wall). The echo's latency/bound carry the
    wall either way."""
    m = re.search(r"decode\s*:\s*\d+ tok in [\d.]+ ms \(([\d.]+) tok/s",
                  stderr or "")
    if m:
        return float(m.group(1))
    return n / (wall_ms / 1000.0) if wall_ms else 0.0


def clean_text(out):
    txt = " ".join(out.split()).replace('"', "'")
    return txt[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run", "census-only"])
    ap.add_argument("--node", default="plain-view")
    ap.add_argument("--pods", type=int, default=6)
    ap.add_argument("--shared-dir", default=DEFAULT_SHARED)
    ap.add_argument("--state-dir",
                    default=os.path.expanduser("~/wavefront-harness/state"))
    args = ap.parse_args()

    os.makedirs(args.state_dir, exist_ok=True)
    lock = open(os.path.join(args.state_dir, "wavefront.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("wavefront: another run holds the lock", flush=True)
        return 0

    pods_reg = os.path.join(args.shared_dir, "wavefront.txt")
    echoes_reg = os.path.join(args.shared_dir, "wavefront-echoes.txt")
    elections_reg = os.path.join(args.shared_dir,
                                  "wavefront-elections.txt")
    names = [f"{POD_PREFIX}{i}" for i in range(args.pods)]
    img_id, img_size = image_info()
    ram = node_ram_bytes()
    print(f"{stamp()} census: image_id={img_id} image_bytes={img_size} "
          f"node_ram_bytes={ram}", flush=True)
    if img_id is None:
        print("WAVEFRONT-RUN-FAIL image-absent", flush=True)
        return 1
    if args.mode == "census-only":
        print("WAVEFRONT-CENSUS-OK", flush=True)
        return 0

    started = []
    try:
        # --- start the resident set ---------------------------------
        for name in names:
            podman("rm", "-f", name)
            r = podman("run", "-d", "-i", "--name", name,
                       "--memory", MEM_LIMIT, "--cpus",
                       str(RESIDENT_CPUS), IMAGE, "--chat",
                       "-t", str(RESIDENT_CPUS))
            if r.returncode != 0:
                print(f"start {name} FAILED: {r.stderr.strip()[:160]}",
                      flush=True)
                continue
            started.append(name)
            print(f"{stamp()} started {name}", flush=True)
        time.sleep(3)
        alive = []
        for name in started:
            r = podman("inspect", "--format", "{{.State.Running}}", name)
            if r.stdout.strip() == "true":
                alive.append(name)
        print(f"{stamp()} resident alive: {alive}", flush=True)
        if not alive:
            print("WAVEFRONT-RUN-FAIL no-resident-pods", flush=True)
            return 1

        # --- pod census block ---------------------------------------
        now = int(time.time())
        lines = [f"wavefront-census {stamp(now)} ee={ee_of(now)}",
                 f"node {args.node} ram_bytes={ram} "
                 f"reserve_bytes={ram // 4} partition_bytes="
                 f"{MEM_LIMIT_BYTES} partitions_held={len(alive)} "
                 f"image={IMAGE} image_id={img_id}"]
        for name in alive:
            lines.append(
                f"pod {name} node={args.node} image={IMAGE} "
                f"image_id={img_id} mem_limit_bytes={MEM_LIMIT_BYTES} "
                f"cpus={RESIDENT_CPUS} state=resident "
                f"rss_bytes={container_rss(name)} probes=0 eligible=yes")
        lines.append("end-wavefront-census")
        append_block(pods_reg, lines)
        print(f"{stamp()} census block appended ({len(alive)} pods)",
              flush=True)

        # --- wait for the EE boundary, then ping --------------------
        boundary, round_ee = next_boundary(int(time.time()))
        wait = boundary - int(time.time())
        print(f"{stamp()} next EE boundary {stamp(boundary)} "
              f"ee={round_ee} in {wait}s", flush=True)
        while time.time() < boundary:
            time.sleep(min(5, boundary - time.time()))

        probe = PROBES["capital-1"]
        hits = sum(1 for n in alive
                   if latest_echo(echoes_reg, n, "capital-1"))
        append_line(echoes_reg,
                    f"schedule {stamp()} ee={round_ee} node={args.node} "
                    f"probe=capital-1 pods={len(alive)} "
                    f"max_concurrent_wakers={MAX_CONCURRENT_WAKERS} "
                    f"wake_gap_s={WAKE_GAP_S} cache_hits={hits} "
                    f"cache_misses={len(alive) - hits}")
        results = {}
        for i, name in enumerate(alive):
            if i:
                time.sleep(WAKE_GAP_S)
            cached = latest_echo(echoes_reg, name, "capital-1")
            cache = "hit" if cached else "miss"
            t0 = time.time()
            r = podman("exec", name, "/asm-llm", "-m", MODEL,
                       "-p", probe["prompt"], "-n", str(probe["n"]),
                       "-t", str(RESIDENT_CPUS), timeout=180)
            wall_ms = int((time.time() - t0) * 1000)
            out = r.stdout or ""
            ok = r.returncode == 0 and bool(out.strip())
            match = probe["expect"].lower() in out.lower()
            tok_s = decode_rate(r.stderr, probe["n"], wall_ms)
            results[name] = {"ok": ok, "match": match,
                             "latency_ms": wall_ms}
            append_line(
                echoes_reg,
                f"echo caller=wavefront hw={name}:{img_id} "
                f"target={name} probe=capital-1 "
                f"ok={'yes' if ok else 'no'} "
                f"match={'yes' if match else 'no'} cache={cache} "
                f"latency_ms={wall_ms} bound_ms={wall_ms} "
                f"tokens={probe['n']} tok_per_s={tok_s:.2f} "
                f"text=\"{clean_text(out)}\"")
            print(f"{stamp()} ping {name}: ok={ok} match={match} "
                  f"latency_ms={wall_ms}", flush=True)

        # --- hot-slot election (elect.py idiom) ---------------------
        echoed = [n for n in alive
                  if results[n]["ok"] and results[n]["match"]]
        if echoed:
            ranked = sorted(echoed,
                            key=lambda n: results[n]["latency_ms"])
            winner = ranked[0]
            runner = ranked[1] if len(ranked) > 1 else "none"
            rscore = (results[runner]["latency_ms"]
                      if runner != "none" else 0)
            append_line(
                elections_reg,
                f"wavefront-election {stamp()} ee={round_ee} "
                f"node={args.node} hot={winner} "
                f"hot_score={results[winner]['latency_ms']} "
                f"runner_up={runner} runner_up_score={rscore} "
                f"rule=lowest-latency-among-echoed "
                f"eligible={len(echoed)}")
            print(f"{stamp()} elected hot={winner}", flush=True)

            # promotion: restart the winner at the hot partition and
            # give it the deeper probe — the round's deep work.
            podman("stop", "-t", "5", winner)
            podman("rm", "-f", winner)
            podman("run", "-d", "-i", "--name", winner,
                   "--memory", MEM_LIMIT, "--cpus", str(HOT_CPUS),
                   IMAGE, "--chat", "-t", str(HOT_CPUS))
            time.sleep(2)
            append_block(pods_reg, [
                f"wavefront-state {stamp()} ee={round_ee}",
                f"pod {winner} node={args.node} state=hot "
                f"cpus={HOT_CPUS} reason=wavefront-election "
                f"ee={round_ee}",
                "end-wavefront-state"])
            deep = PROBES["colors-1"]
            t0 = time.time()
            r = podman("exec", winner, "/asm-llm", "-m", MODEL,
                       "-p", deep["prompt"], "-n", str(deep["n"]),
                       "-t", str(HOT_CPUS), timeout=180)
            wall_ms = int((time.time() - t0) * 1000)
            out = r.stdout or ""
            match = deep["expect"].lower() in out.lower()
            append_line(
                echoes_reg,
                f"echo caller=wavefront hw={winner}:{img_id} "
                f"target={winner} probe=colors-1 ok="
                f"{'yes' if r.returncode == 0 else 'no'} "
                f"match={'yes' if match else 'no'} cache=miss "
                f"latency_ms={wall_ms} bound_ms={wall_ms} "
                f"tokens={deep['n']} "
                f"tok_per_s={decode_rate(r.stderr, deep['n'], wall_ms):.2f} "
                f"text=\"{clean_text(out)}\"")
            print(f"{stamp()} deep probe {winner}: match={match} "
                  f"latency_ms={wall_ms}", flush=True)
        else:
            append_line(elections_reg,
                        f"wavefront-election {stamp()} ee={round_ee} "
                        f"node={args.node} hot=none rule=no-echo "
                        f"eligible=0")
            print(f"{stamp()} no echoes — no election", flush=True)
        print(f"WAVEFRONT-RUN-OK ee={round_ee} pods={len(alive)} "
              f"echoed={len(echoed)}", flush=True)
        return 0
    finally:
        # --- shutdown: the test pods always come down ---------------
        for name in names:
            podman("stop", "-t", "5", name)
            podman("rm", "-f", name)
        now = int(time.time())
        lines = [f"wavefront-state {stamp(now)} ee={ee_of(now)}"]
        for name in names:
            lines.append(f"pod {name} node={args.node} state=stopped "
                         f"reason=live-loop-complete")
        lines.append("end-wavefront-state")
        try:
            append_block(pods_reg, lines)
        except OSError as e:
            print(f"shutdown state append failed: {e}", flush=True)
        print(f"{stamp()} test pods shut down", flush=True)


if __name__ == "__main__":
    sys.exit(main())
