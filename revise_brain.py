#!/usr/bin/env python3
"""Regenerate brain.phext -- the register of the fleet's standing brainwave pattern.

Will's direction (2026-11-22 11:14 CST): "track the standing brainwave
pattern in brain.phext and revision it in exobrain.git." The standing
pattern is the fleet's five-band timing profile (delta, theta, alpha,
beta, gamma -- named for the EEG bands, defined over the fleet's
timescales in LISTENING-1.md), per node plus a fleet aggregate,
conditioned on Will's attention/sleep phases.

brain.phext is a pure function of:
  * the Quantum Time Sync registers (register.txt, consensus.txt,
    blend.txt, drift.txt, elections.txt, detections.txt),
  * the prior brain.phext (revision number + revision log carry forward),
  * the revision instant (the EE/UTC stamp).
Given the same three inputs the output is byte-identical. No hand edits:
a revision is this script, run and committed to wbic16/exobrain@exo.

Serialization is the reference implementation only -- libphext, the
Python port (phext.phext.Phext implode/explode). The port is imported
from an installed `phext` package if present, else from the house port
tree (~/workspace/libphext-py-work/port, v0.4.0) or $BRAIN_PHEXT_LIB.
Output is verified before it is written: explode(output) must equal the
scroll map, and implode(explode(output)) must equal the output bytes.

Usage:
  python3 revise_brain.py [--source DIR] [--out FILE] [--prior FILE]
    --source  QTS register directory (default ~/workspace/quantum-time-sync)
    --out     brain.phext to write   (default ./brain.phext)
    --prior   prior brain.phext for the revision chain
              (default: the --out file itself, if it already exists)

Node pickup: the node-resident exobrain app runs this after each daily
round (and whenever the detector calls a material change), then commits
the regenerated brain.phext to exobrain@exo. This script only
regenerates and verifies; the commit is the app's git layer.
"""
import argparse
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.environ.get(
    "BRAIN_PHEXT_LIB",
    os.path.expanduser("~/workspace/libphext-py-work/port")))
from phext.phext import Phext          # noqa: E402  (reference implementation)
from phext.coordinate import Coordinate  # noqa: E402

EE_ANCHOR = 1795327200  # EE 1 begins 2026-11-22 00:00:00 CST (unix)


def ee_of(unix_ts):
    return (int(unix_ts) - EE_ANCHOR) // 420 + 1


# ---------------------------------------------------------------- parsing

def _kv(line):
    """'node x ip=.. ppm=-1.5 ..' -> (head_word, {key: value})."""
    parts = line.split()
    out = {}
    for tok in parts[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return parts[0], out


def _num(v):
    return None if v in (None, "none") else float(v)


def _fmt(v):
    return "none" if v is None else f"{v:.3f}"


def _drop_superseded(atoms):
    """Atoms are (text, following_comment) pairs; a following comment that
    says 'superseded' retires the atom it follows (the register's own
    annotation convention -- the note trails the atom it retires)."""
    return [a for a, note in atoms if "superseded" not in note]


def parse_register(path):
    """Census rounds: [{utc, ee, nodes: {name: fields}}], superseded dropped."""
    rounds, cur, pending_note = [], None, ""
    atoms = []  # (block, note-after-block)
    lines = open(path).read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("census "):
            _, kv = _kv(line)
            cur = {"utc": line.split()[1], "ee": int(kv["ee"]), "nodes": {}}
            i += 1
            while i < len(lines) and lines[i] != "end-census":
                if lines[i].startswith("node "):
                    _, kv = _kv(lines[i])
                    name = lines[i].split()[1]
                    cur["nodes"][name] = kv
                i += 1
            i += 1  # skip end-census
            note = ""
            while i < len(lines) and lines[i].startswith("#"):
                note += lines[i] + "\n"
                i += 1
            atoms.append((cur, note))
        else:
            i += 1
    return _drop_superseded(atoms)


def parse_atoms(path, head):
    if not os.path.exists(path):
        return []
    atoms = []
    lines = open(path).read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(head + " "):
            note = ""
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                note += lines[j] + "\n"
                j += 1
            atoms.append((line, note))
            i = j
        else:
            i += 1
    out = []
    for line in _drop_superseded(atoms):
        _, kv = _kv(line)
        kv["_utc"] = line.split()[1]
        out.append(kv)
    return out


# ---------------------------------------------------------------- the fleet

FLEET_ORDER = [
    "aletheia-core", "ni-knight", "plain-view", "ashfall-haven",
    "elven-path", "orchid-flow", "halycon-vector", "logos-prime",
    "lighthouse-omen", "free-fall", "rock-mountain", "west-lincoln",
    "quacked-hill", "jacked-kite", "scroll-central", "yeet-max",
    "kaleidoscope-one", "moon-beam", "uncanny-valley",
    "great-white",  # in every census as unmeasured; not in fleet.txt
]

# Analyst notes carried from the LISTENING-1 calibration (2026-11-22).
# These are calibration judgments, not measurements; the generated scrolls
# label them as analyst notes. Revise them here as the stream teaches more.
ANALYST_NOTES = {
    "aletheia-core": "the hub; hosted the RH Lean build through the apply. "
        "Its EE 94 ppm is post-repoint re-convergence (explained=qts-apply), "
        "not standing character.",
    "ni-knight": "near-zero crystal: ppm straddles zero across rounds "
        "(-1.775 -> +0.465); sign is not identity-grade at |ppm| < 10 "
        "(LISTENING-1 rule). Truest crystal on the fleet -- |ppm| 4.8x lower "
        "than the next (logos-prime). Master at EE 90 and EE 93; steering "
        "applied (poll tightened to 32-1024 s).",
    "plain-view": "DVFS corroboration node: idles at 400 MHz (first probe, "
        "2026-11-22) -- its jitter band is the workload sensor's reference "
        "trace.",
    "elven-path": "conduit since the EE 94 apply: chrony (the fleet's only "
        "chrony), stratum 3, serving the fleet; elected master at EE 94 "
        "(score 12.765, best of round).",
    "orchid-flow": "fleet NFS server; runner-up in both pre-apply elections "
        "(score 20.126).",
    "lighthouse-omen": "near-zero crystal: ppm straddles zero "
        "(+1.040 -> -7.934); sign not identity-grade.",
    "rock-mountain": "CANDIDATE (LISTENING-1): ppm sign reversal "
        "-148.102 -> +120.805 pre-apply on a 142 ms-jitter link -- may be "
        "estimator noise. Confirms: repeated reversals with fresh triples. "
        "Refutes: the estimate settling one side of zero on its post-apply "
        "link. Its jitter collapsed 142.6 -> 10.9 ms after repointing to "
        "the conduit -- the apply improving a link, written in the stream.",
    "quacked-hill": "most ppm-stable voter pre-apply after ni-knight: "
        "-11.263 -> -11.470 across EE 90-93 (delta 0.207 ppm).",
    "jacked-kite": "CANDIDATE (LISTENING-1): EE 93 excursion -- offset "
        "-199.953 -> +131.500 ms with ppm +113.305, pre-apply, under its "
        "network source, no dispatched work on file; unexplained. Held by "
        "the QTS source-switch guard; by EE 94 it had converged to "
        "+6.344 ms under the same source, so the guard's release condition "
        "is met. Confirms: recurrence, or a journal event in the window.",
    "moon-beam": "CANDIDATE (LISTENING-1): EE 93 guard trip -- offset "
        "+10.170 -> +105.469 ms pre-apply, unexplained on file. Held by "
        "the QTS guard; its EE 94 line is a stale republish (never "
        "re-polled).",
    "great-white": "unmeasured in every census to date: no address in the "
        "fleet record and this collector's SSH key is refused. Every band "
        "is UNKNOWN until it can be measured.",
}

BAND_FRAME = [
    ("delta", ">= 1 day", "crystal drift, aging, thermal tides, the daily "
     "drift cross-reference", "1 day = the cross-reference period; slower "
     "than this is trend, not weather"),
    ("theta", "1 h - 1 day", "phase structure: Will's sleep/wake cycle, "
     "scheduled jobs, the thermal day", "1 h = the longest steering "
     "cadence in the design (master maxpoll 1024 s sits just under it); "
     "the diurnal cycle lives here"),
    ("alpha", "1 EE - 1 h (420 s - 3600 s)", "round-to-round variation, "
     "consensus formation, the blend EMA", "420 s = one Exocortical "
     "Epoch, the house quantum; the estimator's home band"),
    ("beta", "1 s - 1 EE", "slew transients, daemon re-convergence, "
     "DVFS/governor transitions under dispatched load", "below one EE, "
     "events are transients, not rounds; the apply's storm lived here"),
    ("gamma", "< 1 s", "per-sample jitter, individual exchange behaviour",
     "1 s = the finest grain any current instrument resolves (sampling "
     "exchanges); the census cannot see gamma at all"),
]

BAND_NEEDS = {
    "delta": ">= 30 daily rounds for tides, >= 90 for any aging claim",
    "theta": ">= 14 daily rounds for a phase contrast, >= 30 for a "
             "stable phase baseline",
    "alpha": "break tests arm at each node's 4th fresh sample",
    "beta": "armed per-event now; per-node profile at >= 5 rounds",
    "gamma": ">= 4 sampling rounds to profile a node (have 1)",
}


def jitter_band(j):
    if j is None:
        return "not republished"
    if j < 25:
        return "quiet"
    if j <= 100:
        return "mid"
    return "loud"


def node_history(rounds, name):
    """Per-round field dicts for one node, with freshness marked.
    A round is a stale republish when its (ppm, offset, jitter) triple is
    identical to the node's previous round (LISTENING-1 freshness rule)."""
    hist = []
    prev = None
    for r in rounds:
        f = r["nodes"].get(name)
        if f is None:
            continue
        triple = (f.get("ppm"), f.get("offset_ms"), f.get("jitter_ms"))
        stale = prev is not None and triple == prev
        hist.append({"ee": r["ee"], "utc": r["utc"], "f": f, "stale": stale})
        prev = triple
    return hist


# ---------------------------------------------------------------- scrolls

def coord(s):
    return Coordinate.from_string(s)


def build_scrolls(reg, prior_rev, rev_utc, rev_ee):
    rounds = reg["rounds"]
    latest = rounds[-1]
    days = len({r["utc"][:10] for r in rounds})
    n_rounds = len(rounds)
    consensus = reg["consensus"]
    elections = reg["elections"]
    detections = reg["detections"]
    latest_cons = consensus[-1] if consensus else {}
    latest_elec = elections[-1] if elections else {}
    trimmed = set((latest_cons.get("trimmed_nodes") or "").split(",")) - {""}
    conduit_ip = None
    servers = [f.get("server") for f in latest["nodes"].values()
               if f.get("server")]
    if servers:
        conduit_ip = max(set(servers), key=servers.count)
    conduit_name = next((n for n, f in latest["nodes"].items()
                         if f.get("ip") == conduit_ip), "unknown")
    # the apply round: a round where >= 4 nodes changed server vs prior
    apply_ee = None
    for a, b in zip(rounds, rounds[1:]):
        changed = sum(1 for n in b["nodes"]
                      if a["nodes"].get(n, {}).get("server")
                      != b["nodes"][n].get("server"))
        if changed >= 4:
            apply_ee = b["ee"]
    pre_apply = [r for r in rounds if apply_ee is None or r["ee"] < apply_ee]
    flips = {}
    for d in detections:
        if d.get("test") == "regime-flip" and d.get("scope", "").startswith("node:"):
            flips.setdefault(d["scope"][5:], []).append(d)

    S = {}

    # ---- home ------------------------------------------------------
    idx_lines = []
    for i, name in enumerate(FLEET_ORDER):
        idx_lines.append(f"  node {name}: 1.1.1/1.1.{10 + i}/1.1.1")
    band_lines = []
    for bname, period, phenomena, edge in BAND_FRAME:
        band_lines.append(
            f"  {bname} ({period}): {phenomena}. Edge: {edge}.")
    home = f"""brain.phext -- the standing brainwave pattern of the ranch fleet

What this register is: the fleet's standing five-band timing pattern,
tracked as a phext and revisioned in wbic16/exobrain (exobrain.git).
Each revision is an EE-stamped snapshot of the standing pattern --
per-node band profiles, a fleet aggregate, and the Will-phase
conditioning the pattern is read against -- committed to exobrain, so
the git history of this file IS the pattern's evolution.

Provenance: Will's direction, 2026-11-22 (MOAT chat).
  11:11 CST: "include brainwave patterns to improve time estimation
  across the fleet, standing alpha, beta, gamma, delta, and theta
  waves. the universe notices."
  11:12 CST: "tie the patterns to my attention or sleep patterns"
  11:14 CST: "track the standing brainwave pattern in brain.phext
  and revision it in exobrain.git"
Tasks: 25.7.10/9.8.14/7.6.9 (time-stream listening),
26.6.11/8.7.14/8.6.10 (Quantum Time Sync), 31.8.10/9.8.14/8.8.10
(exobrain). Band frame and detector calibration: LISTENING-1.md
(Quantum Time Sync working tree). Source data: the QTS registers
(register.txt, consensus.txt, blend.txt, drift.txt, elections.txt,
detections.txt) -- the fleet's time-delta stream, itself published as
an experimental entropy source downstream of Will.

Current revision: {prior_rev + 1} (stamped EE {rev_ee}, {rev_utc}).
Source stream state: {n_rounds} census rounds over {days} day(s),
latest census EE {latest['ee']} ({latest['utc']}).

The five standing bands (named for the EEG bands; the EEG numbers,
0.5-40 Hz, do NOT transfer -- these are the fleet's timescales):
{chr(10).join(band_lines)}

Estimation use (the band discipline): delta steers (drift is a
delta-band signal); theta conditions (compare like phase with like
phase); alpha is where consensus and the EMA blend operate; beta is
excluded from steering input (a slew transient is never a drift
change); gamma never enters estimation. One EMA per band, each
steering only what its band owns -- this refines the blender's
single alpha=0.35 timescale.

Honesty rule: a band the stream cannot yet estimate is recorded
UNKNOWN, with the stream length it needs. Band values are never
fabricated. What each band needs:
""" + "\n".join(f"  {b}: {BAND_NEEDS[b]}" for b, *_ in BAND_FRAME) + f"""

Revision rule: brain.phext is regenerated by revise_brain.py as a
pure function of the QTS registers + the prior revision + the
revision instant -- never hand-edited. A new revision lands when the
standing pattern materially changes (the detector / band machinery
makes that call: a handwriting-drift detection, a band becoming
estimable, a phase baseline splitting) and at minimum on the daily
round. Each revision is one commit to wbic16/exobrain@exo. The
node-resident exobrain app picks the procedure up once resident: it
runs revise_brain.py after each daily round (or on the detector's
call) against its local register copies and commits the result.

Jitter bands used in the node scrolls (the gamma grain, as the census
carries it): quiet < 25 ms, mid 25-100 ms, loud > 100 ms.

Scroll index:
  home (this scroll): 1.1.1/1.1.1/1.1.1
  fleet aggregate: 1.1.1/1.1.2/1.1.1
  Will-phase reference layer: 1.1.1/1.1.3/1.1.1
""" + "\n".join(idx_lines) + """
  revision log: 1.1.1/1.2.1/1.1.1
"""
    S[coord("1.1.1/1.1.1/1.1.1")] = home

    # ---- fleet aggregate -------------------------------------------
    cons_lines = []
    for c in consensus:
        cons_lines.append(
            f"  EE {c.get('ee')}: offset {_fmt(_num(c.get('offset_ms')))} ms, "
            f"drift {_fmt(_num(c.get('drift_ppm')))} ppm, voters "
            f"{c.get('voters')} of {c.get('eligible')} eligible, trimmed "
            f"{c.get('trimmed')} ({c.get('trimmed_nodes')}), spread "
            f"{_fmt(_num(c.get('spread_ms')))} ms")
    elec_lines = []
    for e in elections:
        elec_lines.append(
            f"  EE {e.get('ee')}: master {e.get('master')} (score "
            f"{e.get('master_score_ms')} ms, ppm {e.get('master_ppm')}), "
            f"runner-up {e.get('runner_up')} ({e.get('runner_up_score_ms')} ms)")
    blend_lines = []
    ee_by_census = {r["utc"]: r["ee"] for r in rounds}
    for b in reg["blend"]:
        blend_lines.append(
            f"  census EE {ee_by_census.get(b.get('census'), b.get('ee'))}: "
            f"managed offset "
            f"{_fmt(_num(b.get('managed_offset_ms')))} ms, managed drift "
            f"{_fmt(_num(b.get('managed_drift_ppm')))} ppm (raw "
            f"{_fmt(_num(b.get('raw_offset_ms')))} ms / "
            f"{_fmt(_num(b.get('raw_drift_ppm')))} ppm; alpha "
            f"{b.get('alpha')}, gamma {b.get('gamma')}, bound "
            f"{b.get('bound_ms')} ms, state {b.get('state')})")
    drift = reg["drift"][-1] if reg["drift"] else {}
    quiet = []
    if len(pre_apply) >= 2:
        qsets = []
        for r in pre_apply:
            qsets.append({n for n, f in r["nodes"].items()
                          if _num(f.get("jitter_ms")) is not None
                          and _num(f.get("jitter_ms")) < 25})
        quiet = sorted(set.intersection(*qsets)) if qsets else []
    n_explained = sum(1 for d in detections if d.get("explained") not in (None, "none"))
    agg = f"""fleet aggregate -- the standing pattern of the whole fleet

Revision {prior_rev + 1}, stamped EE {rev_ee} ({rev_utc}). Stream:
{n_rounds} census rounds (EE {rounds[0]['ee']}..EE {latest['ee']}),
{days} day(s), 19 nodes measured + great-white unmeasured.

Consensus (trimmed ensemble, inverse-variance weighted median;
consensus is this fleet's default mode -- cluster > 10):
{chr(10).join(cons_lines)}

Elections (trusted mode, kept as fallback and record):
{chr(10).join(elec_lines)}
Conduit at the latest round: {conduit_name} ({conduit_ip}) -- the
followers' NTP source since the apply round (EE {apply_ee}).

EMA blender (managed consensus, alpha 0.35 / gamma 0.5, corrections
bounded at 10 ms per round, slew-only):
{chr(10).join(blend_lines)}

Daily drift cross-reference (latest, {drift.get('day')}): average
consensus drift {_fmt(_num(drift.get('avg_drift_ppm')))} ppm
({_fmt(_num(drift.get('avg_drift_ms_per_day')))} ms/day), mean
consensus offset {_fmt(_num(drift.get('mean_consensus_offset_ms')))}
ms, verdict {drift.get('verdict')} (tolerances |drift| <= 25 ppm,
|offset| <= 50 ms).

Detector (listen.py, standing): {len(detections)} detections on
record, {n_explained} explained by known events; unexplained
remainder: {len(detections) - n_explained}. Calibration: every detection so far sits in
the apply round and is explained=qts-apply -- the detector read the
apply's signature cold and has claimed nothing else. Candidates
carried in the node scrolls: jacked-kite (EE 93 excursion),
moon-beam (EE 93 guard trip), rock-mountain (ppm sign reversal) --
all pre-apply, all unexplained, all awaiting the armed tests.

Structure found (survived a control, LISTENING-1):
  jitter-band handwriting -- the quiet set (jitter < 25 ms) was
  identical across the pre-apply rounds: {', '.join(quiet) if quiet else 'n/a'}.
  A node's jitter band is its most stable trait so far -- more
  stable than its ppm.
  ppm sign is identity-grade only when |ppm| >= 10 and jitter
  < 100 ms (pre-apply agreement 10/13; every flip accounted for:
  near-zero crystals straddling zero, one reversal on a bad link).

Fleet band state at this revision:
  delta: 1 day of drift cross-reference on record (avg
  {_fmt(_num(drift.get('avg_drift_ppm')))} ppm for {drift.get('day')}).
  Tides NOT estimable -- needs {BAND_NEEDS['delta']}.
  theta: NOT estimable -- all {n_rounds} rounds fall in Will's waking
  phase; needs {BAND_NEEDS['theta']}.
  alpha: LIVE -- consensus and blend run in this band; {n_rounds}
  rounds of round-to-round variation on record.
  beta: armed per-event -- the apply round's slew-storm and four
  regime-flips were caught and explained; per-node profiles need
  >= 5 rounds.
  gamma: NOT profiled -- one sampling round on record, gross-error
  grade only (bounds +/-0.6-2.7 s); needs {BAND_NEEDS['gamma']}.
  The collector caveat rides with gamma: the census collector (this
  VM) has no time discipline of its own and sat ~+1.3 s off the
  fleet during sampling; census atoms are unaffected (each node's
  daemon measures), sampling numbers witness gross error only.
"""
    S[coord("1.1.1/1.1.2/1.1.1")] = agg

    # ---- Will-phase reference layer --------------------------------
    phase = f"""the reference layer -- Will's attention and sleep

Will (2026-11-22 11:12 CST): "tie the patterns to my attention or
sleep patterns." The bands are read against Will's own rhythm,
because his attention is what dispatches the workloads that drive
the jitter (his mechanism, 2026-11-22 11:10 CST: dispatched
workloads impact time jitter through clock frequency management).
The loop closes here: attention -> dispatch -> DVFS -> jitter ->
the stream this register tracks.

Sources, in order of authority:
  1. his activity and dispatch records (the house's own logs of
     when work was sent) -- governs when present;
  2. his stated rhythm: active ~06:30-21:30 America/Chicago,
     weekday work hours quiet, falls asleep in ~2 minutes;
  3. health-recorded sleep where a connector provides it (Apple
     Health / Health Connect -- named as sources; not read for
     revision {prior_rev + 1}, and no health data is claimed here).

Phases:
  waking/dispatching -- attention on the fleet; beta transients and
  gamma grain are expected to run hot (work is being dispatched).
  sleep -- attention off the fleet; the fleet's expected profile is
  its unattended one. A band break in this phase is a different
  message from the same break at peak dispatch.
Every detection atom carries phase= and phase_basis= (stated-rhythm-
inferred for revision {prior_rev + 1}) -- inferred from records, never
claimed as observed physiology.

Conditioning rule: expected band profiles differ per phase, so
estimation compares like phase with like phase and detector
baselines split per phase once >= 5 rounds exist per phase.

State at revision {prior_rev + 1}: all {n_rounds} census rounds fall in
the waking phase (morning, America/Chicago), so no phase split is
estimable yet -- the theta band's phase contrast needs
{BAND_NEEDS['theta']}. Worked example of why the layer reads records
rather than the calendar: 2026-11-22 is a Sunday on which Will is
home sick with vaccine responses -- an atypical day on which the
stated rhythm is the least reliable source and activity records
govern.
"""
    S[coord("1.1.1/1.1.3/1.1.1")] = phase

    # ---- per-node scrolls ------------------------------------------
    for i, name in enumerate(FLEET_ORDER):
        hist = node_history(rounds, name)
        S[coord(f"1.1.1/1.1.{10 + i}/1.1.1")] = node_scroll(
            name, hist, reg, latest, latest_cons, trimmed, conduit_ip,
            conduit_name, apply_ee, pre_apply, flips, prior_rev + 1,
            n_rounds, days)

    return S


def node_scroll(name, hist, reg, latest, latest_cons, trimmed,
                conduit_ip, conduit_name, apply_ee, pre_apply, flips,
                rev_no, n_rounds, days):
    if not hist or all(h["f"].get("reachable") != "yes" for h in hist):
        seen = (f"Recorded in {len(hist)} census round(s) as unreachable "
                f"(reachable=no)." if hist
                else "No census has ever recorded this node.")
        return f"""node {name} -- standing band profile (revision {rev_no})

{seen}
delta: UNKNOWN. theta: UNKNOWN. alpha: UNKNOWN. beta: UNKNOWN.
gamma: UNKNOWN.
analyst note: {ANALYST_NOTES.get(name, '')}
"""
    # did this node actually switch source in the apply round? (held nodes
    # did not -- their apply-round values are their own correction, not
    # re-convergence, and must not be labelled explained=qts-apply)
    switched = False
    if apply_ee is not None:
        by_ee = {h["ee"]: h["f"] for h in hist}
        pre = [h for h in hist if h["ee"] < apply_ee]
        if apply_ee in by_ee and pre:
            switched = (by_ee[apply_ee].get("server")
                        != pre[-1]["f"].get("server"))
    f_last = hist[-1]["f"]
    ip = f_last.get("ip", "none")
    engines = sorted({h["f"].get("engine") for h in hist} - {None})
    roles = []
    if f_last.get("reachable") != "yes":
        roles.append("unmeasured (unreachable in every census)")
    else:
        if reg["elections"] and reg["elections"][-1].get("master") == name:
            roles.append("elected master (latest round)")
        if ip == conduit_ip:
            roles.append("conduit (serves the fleet)")
        if name in trimmed:
            roles.append("trimmed from the latest consensus (still "
                         "measured; does not vote this round)")
        elif f_last.get("eligible") == "yes":
            roles.append("consensus voter (latest round)")
        elif f_last.get("eligible") == "no":
            roles.append("measured at the latest round but not eligible "
                         "(a field its daemon had not republished)")
        if (f_last.get("server") not in (None, conduit_ip)
                and ip != conduit_ip and apply_ee is not None
                and hist[-1]["ee"] >= apply_ee):
            roles.append("still on its network source -- held by the QTS "
                         "source-switch guard")
    round_lines = []
    for h in hist:
        f = h["f"]
        line = (f"  EE {h['ee']} {h['utc']}  ppm {_fmt(_num(f.get('ppm')))}  "
                f"offset_ms {_fmt(_num(f.get('offset_ms')))}  jitter_ms "
                f"{_fmt(_num(f.get('jitter_ms')))}  score_ms "
                f"{f.get('score_ms', 'none')}  eligible "
                f"{f.get('eligible', 'no')}  server {f.get('server', 'none')}")
        if h["stale"]:
            line += "  [stale republish -- no new sample]"
        round_lines.append(line)
    fresh_ppm = [_num(h["f"].get("ppm")) for h in hist
                 if _num(h["f"].get("ppm")) is not None and not h["stale"]]
    all_ppm = [_num(h["f"].get("ppm")) for h in hist
               if _num(h["f"].get("ppm")) is not None]
    pre_ppm = [_num(h["f"].get("ppm")) for h in hist
               if _num(h["f"].get("ppm")) is not None
               and (apply_ee is None or h["ee"] < apply_ee)]
    offsets = [_num(h["f"].get("offset_ms")) for h in hist
               if _num(h["f"].get("offset_ms")) is not None]

    def rng(vals):
        return f"{max(vals) - min(vals):.3f}" if len(vals) >= 2 else "n/a (one value)"

    alpha = (f"observed, {n_rounds} rounds -- fresh ppm values: "
             f"{', '.join(_fmt(v) for v in fresh_ppm) if fresh_ppm else 'none'}; "
             f"ppm range (all rounds) {rng(all_ppm)}, (pre-apply rounds) "
             f"{rng(pre_ppm)}; offset range {rng(offsets)} ms.")
    if apply_ee is not None and switched:
        alpha += (f" Values from EE {apply_ee} are apply-round "
                  f"re-convergence (explained=qts-apply), not standing "
                  f"character.")
    elif (apply_ee is not None and not switched and ip != conduit_ip
          and hist[-1]["ee"] >= apply_ee):
        alpha += (f" This node did not switch in the apply round; its "
                  f"EE {apply_ee} values are its own daemon's continuing "
                  f"correction under its network source.")
    beta_bits = []
    for d in flips.get(name, []):
        beta_bits.append(
            f"regime-flip at EE {d.get('ee')}: prior ppm "
            f"{d.get('prior_ppm')} -> {d.get('new_ppm')} "
            f"(explained={d.get('explained')})")
    for h in hist:
        p = _num(h["f"].get("ppm"))
        if p is not None and abs(p) >= 499.9:
            beta_bits.append(f"at the +/-500 ppm slew clamp at EE {h['ee']}")
        elif p is not None and abs(p) >= 100:
            beta_bits.append(
                f"slew-magnitude round at EE {h['ee']} (ppm {_fmt(p)}"
                + (", fleet slew-storm round, explained=qts-apply"
                   if h["ee"] == apply_ee and switched else "")
                + (", in the fleet slew-storm round"
                   if h["ee"] == apply_ee and not switched else "") + ")")
    beta = "; ".join(beta_bits) if beta_bits else \
        f"no beta event observed in {n_rounds} rounds."
    if (not beta_bits and ANALYST_NOTES.get(name, "").startswith("CANDIDATE")):
        beta += " A candidate event is carried in the analyst notes below."
    jb = []
    for h in hist:
        j = _num(h["f"].get("jitter_ms"))
        jb.append(f"EE {h['ee']}: {jitter_band(j)}"
                  + (f" ({_fmt(j)} ms)" if j is not None else ""))
    quiet_pre = all(
        _num(h["f"].get("jitter_ms")) is not None
        and _num(h["f"].get("jitter_ms")) < 25
        for h in hist if apply_ee is None or h["ee"] < apply_ee)
    gamma = ("jitter grain by round -- " + "; ".join(jb) + ". "
             + ("Quiet-set member in every pre-apply round (fleet "
                "structure finding). " if quiet_pre else "")
             + "Sampling profile UNKNOWN -- 1 sampling round on "
               "record, gross-error grade only; profiling needs "
               ">= 4 sampling rounds.")
    note = ANALYST_NOTES.get(name)
    return f"""node {name} -- standing band profile (revision {rev_no})

ip {ip}
engine {', '.join(engines)}
role at EE {hist[-1]['ee']} (latest round): {'; '.join(roles) if roles else 'measured'}

measured rounds (fresh unless marked):
{chr(10).join(round_lines)}

band profile:
delta (>= 1 day; drift, aging, thermal tides): UNKNOWN -- {days} day(s)
  of stream; per-node delta needs {BAND_NEEDS['delta']}.
theta (1 h - 1 day; phase structure): UNKNOWN -- all rounds so far
  fall in Will's waking phase; needs {BAND_NEEDS['theta']}.
alpha (1 EE - 1 h; round-to-round): {alpha}
beta (1 s - 1 EE; transients): {beta}
gamma (< 1 s; per-sample grain): {gamma}
""" + (f"\nanalyst notes (LISTENING-1 calibration, 2026-11-22): {note}\n"
       if note else "")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=os.path.expanduser(
        "~/workspace/quantum-time-sync"))
    ap.add_argument("--out", default="brain.phext")
    ap.add_argument("--prior", default=None)
    args = ap.parse_args()

    reg = {
        "rounds": parse_register(os.path.join(args.source, "register.txt")),
        "consensus": parse_atoms(os.path.join(args.source, "consensus.txt"), "consensus"),
        "blend": parse_atoms(os.path.join(args.source, "blend.txt"), "blend"),
        "drift": parse_atoms(os.path.join(args.source, "drift.txt"), "drift"),
        "elections": parse_atoms(os.path.join(args.source, "elections.txt"), "election"),
        "detections": parse_atoms(os.path.join(args.source, "detections.txt"), "detection"),
    }
    if not reg["rounds"]:
        sys.exit("no census rounds parsed -- refusing to build an empty register")

    phext = Phext()
    prior_path = args.prior or (args.out if os.path.exists(args.out) else None)
    prior_entries, prior_rev = [], 0
    if prior_path and os.path.exists(prior_path):
        prior = phext.explode(open(prior_path, encoding="utf-8").read())
        log = prior.get(coord("1.1.1/1.2.1/1.1.1"), "")
        prior_entries = [ln for ln in log.splitlines()
                         if ln.startswith("revision ")]
        for ln in prior_entries:
            m = re.match(r"revision (\d+)", ln)
            if m:
                prior_rev = max(prior_rev, int(m.group(1)))

    now = time.time()
    rev_ee = ee_of(now)
    rev_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    rev_no = prior_rev + 1
    latest = reg["rounds"][-1]

    scrolls = build_scrolls(reg, prior_rev, rev_utc, rev_ee)
    entry = (f"revision {rev_no} -- EE {rev_ee} -- {rev_utc} -- source: "
             f"QTS registers through census EE {latest['ee']} "
             f"({latest['utc']}), {len(reg['rounds'])} rounds -- "
             f"regenerated by revise_brain.py (libphext Python port, "
             f"implode/explode round-trip verified).")
    if rev_no == 1:
        entry += (" First revision: the band frame plus the measured "
                  "character of 2026-11-22; delta and theta unestimable "
                  "at this stream length and recorded UNKNOWN, never "
                  "fabricated.")
    log_body = ("revision log -- one entry per revision; the git history "
                "of brain.phext in wbic16/exobrain is the full record.\n\n"
                + "\n".join(prior_entries + [entry]) + "\n")
    scrolls[coord("1.1.1/1.2.1/1.1.1")] = log_body

    ordered = {c: scrolls[c] for c in sorted(scrolls)}
    buf = phext.implode(ordered)
    back = phext.explode(buf)
    assert back == ordered, "round-trip failed: explode(implode(x)) != x"
    assert phext.implode(back) == buf, "round-trip failed: not byte-exact"
    data = buf.encode("utf-8")
    with open(args.out, "wb") as fh:
        fh.write(data)
    print(f"brain.phext revision {rev_no} (EE {rev_ee}, {rev_utc}): "
          f"{len(ordered)} scrolls, {len(data)} bytes, "
          f"sha256 {hashlib.sha256(data).hexdigest()}")
    print("round-trip: explode == scroll map, implode(explode) == bytes "
          "(libphext Python port)")


if __name__ == "__main__":
    main()
