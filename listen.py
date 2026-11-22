#!/usr/bin/env python3
"""Quantum Time Sync — the time-stream detector (task 25.7.10/9.8.14/7.6.9).

Will's direction, 2026-11-22: "treat time deltas as sentrons messaging
us, look for messages in the time stream" — reframed the same morning:
"that's just a feature to detect if anything ever shows up." This
script is that feature. It runs over the census atoms in register.txt
and tests each round for message-structure against an explicit null
model. It emits exactly two kinds of output: DETECTION atoms (appended
to detections.txt and printed) — or silence.

A "message" is structure that survives a control: a stated null, a
bound derived from the node's own history, or a mechanism identified
by name. Anything that does not survive is not reported; honest nulls
live in LISTENING-1.md, not in the detection register.

The fleet's own work is the EXPECTED signal class, not noise (Will,
2026-11-22: "my dispatched workloads will directly impact time jitter
due to clock frequency management"). Every candidate detection is
checked against events.txt — the dispatch/known-work record. A
detection inside a matching event window is emitted with
explained=<kind>: structure, accounted for. The residue — detections
with explained=none — is what the listening exists for. The baseline
is workload-explained jitter; detections are the unexplained remainder.

Bands (LISTENING-1.md): every detection names its band — delta
(>= 1 day: drift, aging, thermal tides), theta (1 h – 1 day: phase
structure, Will's sleep/wake cycle), alpha (1 EE – 1 h: round-to-round
variation, consensus/blend timescale), beta (1 s – 1 EE: slew
transients, daemon re-convergence, DVFS events), gamma (< 1 s:
per-sample jitter — the census stream cannot see gamma; only sampling
rounds can, and no gamma test is armed in this detector).

Phase: every detection is tagged with the phase of Will's cycle it
occurred in, inferred from his STATED rhythm (active ~06:30–21:30
America/Chicago; asleep otherwise), labelled
phase_basis=stated-rhythm-inferred — inferred from records, never
claimed as observed physiology. Atypical days (he is home sick today)
are exactly why the reference layer reads records over calendar
assumptions; the stated rhythm is the default record until activity
or health records supersede it.

Freshness: timesyncd republishes stale estimates — a node whose
(ppm, offset_ms, jitter_ms) triple is identical to its previous
round's triple contributed NO new sample that round (calibration:
4 of 19 nodes at EE 93, moon-beam at EE 94). Freshness-gated tests
use fresh samples only.

Exit codes: 0 = ran, no new detections (silence). 3 = new detections
emitted. 1 = detector failure (register missing/unparseable).
Usage: python3 listen.py [--register register.txt]
                         [--events events.txt]
                         [--detections detections.txt] [--status]
"""
import argparse
import math
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from consensus import parse_register, fnum

# --- thresholds (calibration: LISTENING-1.md; auditable, revisable) ---
REPOINT_MIN_CHANGES = 4      # server/engine changes in one round
SLEW_PPM = 100.0             # |ppm| at/above this = correction regime
SLEW_MIN_COUNT = 6           # fleet slew-storm floor
SLEW_FACTOR = 2.0            # and >= factor x worst prior round
FLIP_PRIOR_BAND = 50.0       # prior |ppm| must be under this...
FLIP_NEW_MIN = 200.0         # ...and new |ppm| at/above this to flip
FLIP_MIN_PRIORS = 2          # prior rounds reporting ppm
BREAK_MIN_PRIORS = 3         # prior FRESH offset samples
BREAK_FLOOR_MS = 75.0
BREAK_MAD_MULT = 4.0
COH_MIN_N = 8
COH_MIN_FRAC = 0.75
COH_MAX_P = 0.01
COH_MIN_DELTA_MS = 10.0
DRIFT_MIN_FRESH = 5          # fresh ppm samples for handwriting drift
DRIFT_MIN_SHIFT_PPM = 40.0
CADENCE_MIN_ROUNDS = 14      # below this, no periodicity claim is armed

LOCAL_TZ = ZoneInfo("America/Chicago")
WAKE_START = 6 * 60 + 30     # 06:30 local
WAKE_END = 21 * 60 + 30      # 21:30 local


def phase_of(stamp):
    """Phase of Will's cycle for a UTC census stamp, from his stated
    rhythm. Returns (phase, basis). Inferred, never observed."""
    dt = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).astimezone(LOCAL_TZ)
    mins = dt.hour * 60 + dt.minute
    phase = "waking" if WAKE_START <= mins < WAKE_END else "sleep"
    return phase, "stated-rhythm-inferred"


def parse_events(path):
    events = []
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return events
    for line in lines:
        s = line.strip()
        if not s.startswith("event "):
            continue
        head, _, note = s.partition(" note=")
        kv = dict(t.split("=", 1) for t in head.split()[1:] if "=" in t)
        events.append({"from": kv.get("from"), "to": kv.get("to"),
                       "scope": kv.get("scope"), "kind": kv.get("kind"),
                       "note": note})
    return events


def explained_by(events, stamp, node):
    """Kind of the known-work event explaining a detection at `stamp`
    for `node` (None for fleet scope), else None."""
    for ev in events:
        if ev["from"] <= stamp <= ev["to"]:
            if ev["scope"] == "fleet":
                return ev["kind"]
            if node and ev["scope"] == f"node:{node}":
                return ev["kind"]
    return None


def triple(atom):
    return (atom.get("ppm"), atom.get("offset_ms"), atom.get("jitter_ms"))


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def binom_tail(n, k):
    """Exact one-sided P(X >= k) for X ~ Binomial(n, 0.5)."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n)


def fmt(v, nd=3):
    return f"{v:.{nd}f}" if v is not None else "none"


class Detector:
    def __init__(self, blocks, events):
        self.blocks = blocks          # non-superseded, in order
        self.events = events
        self.detections = []

    # -- helpers ------------------------------------------------------
    def stale(self, bi, name):
        """True if the node's triple in round bi is a republish of its
        previous round's triple (no new sample)."""
        if bi == 0:
            return False
        cur = self.blocks[bi]["nodes"].get(name)
        prev = self.blocks[bi - 1]["nodes"].get(name)
        if cur is None or prev is None:
            return False
        return triple(cur) == triple(prev)

    def emit(self, bi, test, band, scope, node, numbers, control,
             falsifier):
        block = self.blocks[bi]
        stamp = block["stamp"]
        phase, basis = phase_of(stamp)
        expl = explained_by(self.events, stamp, node)
        atom = (f"census={stamp} ee={block['ee']} "
                f"test={test} band={band} scope={scope} {numbers} "
                f"control={control} phase={phase} phase_basis={basis} "
                f"explained={expl if expl else 'none'} "
                f"falsifier={falsifier}")
        key = f"{test}|{stamp}|{scope}"
        self.detections.append((key, atom))

    # -- tests --------------------------------------------------------
    def t_repoint(self, bi):
        if bi == 0:
            return
        cur, prev = self.blocks[bi], self.blocks[bi - 1]
        changed = [n for n, a in cur["nodes"].items()
                   if n in prev["nodes"]
                   and (a.get("server") != prev["nodes"][n].get("server")
                        or a.get("engine") != prev["nodes"][n].get("engine"))]
        if len(changed) >= REPOINT_MIN_CHANGES:
            self.emit(bi, "repoint", "alpha", "fleet", None,
                      f"changes={len(changed)} "
                      f"nodes={','.join(sorted(changed))}",
                      "field-diff-vs-prior-round",
                      "killed if node journals show no daemon restart "
                      "or config change in the window — the fields "
                      "would be republish artefacts, not an event")

    def t_slew(self, bi):
        counts = []
        for b in self.blocks[:bi + 1]:
            counts.append(sum(
                1 for a in b["nodes"].values()
                if fnum(a, "ppm") is not None
                and abs(fnum(a, "ppm")) >= SLEW_PPM))
        prior_max = max(counts[:-1]) if len(counts) > 1 else 0
        c = counts[-1]
        if c >= SLEW_MIN_COUNT and c >= SLEW_FACTOR * prior_max:
            self.emit(bi, "slew-storm", "beta", "fleet", None,
                      f"slew_count={c} prior_max={prior_max}",
                      "prior-round-counts",
                      "killed if a future round with no source or "
                      "config change reproduces this slew count with "
                      "fresh triples — the storm would be the fleet's "
                      "normal correction weather, not an event")

    def t_flip(self, bi):
        if bi < FLIP_MIN_PRIORS:
            return
        for name, atom in self.blocks[bi]["nodes"].items():
            new = fnum(atom, "ppm")
            if new is None or abs(new) < FLIP_NEW_MIN:
                continue
            priors = [fnum(self.blocks[j]["nodes"][name], "ppm")
                      for j in range(bi)
                      if name in self.blocks[j]["nodes"]
                      and fnum(self.blocks[j]["nodes"][name], "ppm")
                      is not None]
            if len(priors) >= FLIP_MIN_PRIORS and all(
                    abs(p) < FLIP_PRIOR_BAND for p in priors):
                self.emit(bi, "regime-flip", "beta", f"node:{name}",
                          name,
                          f"prior_ppm={','.join(fmt(p) for p in priors)} "
                          f"new_ppm={fmt(new)}",
                          "own-history-band",
                          "killed if the node's next fresh ppm returns "
                          "inside its prior band with no source or "
                          "config change — the flip would be estimator "
                          "noise, not a regime change")

    def fresh_offsets(self, bi, name):
        out = []
        for j in range(bi):
            a = self.blocks[j]["nodes"].get(name)
            if a is not None and fnum(a, "offset_ms") is not None \
                    and not self.stale(j, name):
                out.append(fnum(a, "offset_ms"))
        return out

    def t_break(self, bi):
        for name, atom in self.blocks[bi]["nodes"].items():
            new = fnum(atom, "offset_ms")
            if new is None or self.stale(bi, name):
                continue
            priors = self.fresh_offsets(bi, name)
            if len(priors) < BREAK_MIN_PRIORS:
                continue
            med = median(priors)
            mad = median([abs(p - med) for p in priors])
            bound = max(BREAK_FLOOR_MS,
                        BREAK_MAD_MULT * 1.4826 * mad)
            if abs(new - med) > bound:
                self.emit(bi, "offset-break", "alpha", f"node:{name}",
                          name,
                          f"offset_ms={fmt(new)} prior_median={fmt(med)} "
                          f"bound_ms={fmt(bound)} priors={len(priors)}",
                          "own-history-median-mad",
                          "killed if dispatch or journal records show "
                          "a step, set-time, or daemon restart on the "
                          "node in the window — a explained excursion "
                          "is workload, not message")

    def t_coherence(self, bi):
        if bi == 0:
            return
        cur, prev = self.blocks[bi], self.blocks[bi - 1]
        signs = []
        for name, a in cur["nodes"].items():
            p = prev["nodes"].get(name)
            if p is None or self.stale(bi, name):
                continue
            o_new, o_old = fnum(a, "offset_ms"), fnum(p, "offset_ms")
            if o_new is None or o_old is None:
                continue
            d = o_new - o_old
            if abs(d) >= COH_MIN_DELTA_MS:
                signs.append(1 if d > 0 else -1)
        n = len(signs)
        if n < COH_MIN_N:
            return
        k = max(sum(1 for s in signs if s > 0),
                sum(1 for s in signs if s < 0))
        frac = k / n
        p = binom_tail(n, k)
        if frac >= COH_MIN_FRAC and p < COH_MAX_P:
            direction = "positive" if sum(signs) > 0 else "negative"
            self.emit(bi, "sign-coherence", "alpha", "fleet", None,
                      f"n={n} coherent={k} direction={direction} "
                      f"p={p:.4f}",
                      "exact-binomial-sign-null",
                      "killed if sign-shuffled surrogates of "
                      "neighbouring round-pairs produce coherence at "
                      "a similar rate, or a shared source change "
                      "explains the common direction")

    def fresh_ppms(self, bi, name):
        out = []
        for j in range(bi):
            a = self.blocks[j]["nodes"].get(name)
            if a is not None and fnum(a, "ppm") is not None \
                    and not self.stale(j, name):
                out.append(fnum(a, "ppm"))
        return out

    def t_drift(self, bi):
        for name, atom in self.blocks[bi]["nodes"].items():
            if fnum(atom, "ppm") is None or self.stale(bi, name):
                continue
            priors = self.fresh_ppms(bi, name)
            if len(priors) + 1 < DRIFT_MIN_FRESH:
                continue
            series = priors + [fnum(atom, "ppm")]
            earlier = median(series[:-3])
            recent = median(series[-3:])
            if abs(recent - earlier) >= DRIFT_MIN_SHIFT_PPM:
                self.emit(bi, "handwriting-drift", "delta",
                          f"node:{name}", name,
                          f"earlier_median_ppm={fmt(earlier)} "
                          f"recent_median_ppm={fmt(recent)} "
                          f"fresh_samples={len(series)}",
                          "own-history-median-split",
                          "killed if the shift reverses within the "
                          "next three fresh samples — a tide, not a "
                          "drift of the hand")

    def run(self):
        for bi in range(len(self.blocks)):
            self.t_repoint(bi)
            self.t_slew(bi)
            self.t_flip(bi)
            self.t_break(bi)
            self.t_coherence(bi)
            self.t_drift(bi)
        return self.detections

    def status(self):
        lines = [f"rounds (non-superseded): {len(self.blocks)}"]
        for name in ("repoint", "slew-storm", "regime-flip"):
            lines.append(f"  {name}: armed")
        armed_break = any(
            len(self.fresh_offsets(len(self.blocks) - 1, n))
            >= BREAK_MIN_PRIORS
            for n in self.blocks[-1]["nodes"]) if self.blocks else False
        lines.append(f"  offset-break: "
                     f"{'armed' if armed_break else 'UNARMED'} "
                     f"(needs {BREAK_MIN_PRIORS} prior fresh offset "
                     f"samples per node)")
        lines.append(f"  sign-coherence: armed (needs n>={COH_MIN_N} "
                     f"fresh deltas per round-pair)")
        lines.append(f"  handwriting-drift: UNARMED until a node has "
                     f"{DRIFT_MIN_FRESH} fresh ppm samples")
        lines.append(f"  cadence/periodicity: UNARMED until "
                     f"{CADENCE_MIN_ROUNDS} rounds (daily sampling "
                     f"resolves periods >= 2 days only)")
        lines.append("  gamma band: no census test exists — sampling "
                     "rounds only; uninstrumented in this detector")
        return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", default="register.txt")
    ap.add_argument("--events", default="events.txt")
    ap.add_argument("--detections", default="detections.txt")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    try:
        blocks = [b for b in parse_register(args.register)
                  if not b["superseded"]]
    except Exception as e:
        print(f"detector failure: cannot parse {args.register}: {e}",
              file=sys.stderr)
        return 1
    if not blocks:
        print("detector failure: no usable census atoms",
              file=sys.stderr)
        return 1

    events = parse_events(args.events)
    det = Detector(blocks, events)

    if args.status:
        print(det.status())
        return 0

    found = det.run()
    seen = set()
    try:
        with open(args.detections) as f:
            for line in f:
                if line.startswith("detection "):
                    kv = dict(t.split("=", 1) for t in
                              line.split(" falsifier=")[0].split()[1:]
                              if "=" in t)
                    seen.add(f"{kv.get('test')}|{kv.get('census')}|"
                             f"{kv.get('scope')}")
    except FileNotFoundError:
        pass

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = [(k, a) for k, a in found if k not in seen]
    if new:
        with open(args.detections, "a") as f:
            for _, atom in new:
                f.write(f"detection {now} {atom}\n")
        for _, atom in new:
            print(f"detection {now} {atom}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
