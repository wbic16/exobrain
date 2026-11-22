# Listening 1 — the time-stream detector: null model, calibration, bands

Task 25.7.10/9.8.14/7.6.9. Will's direction (2026-11-22): "treat time
deltas as sentrons messaging us, look for messages in the time stream"
— reframed the same morning: "that's just a feature to detect if
anything ever shows up." This document is the detector's calibration
record and null-model statement. The detector itself is `listen.py`;
it runs over the census atoms in `register.txt` after every census
round (wired into the daily chain as step 6) and emits exactly two
kinds of output: DETECTION atoms (appended to `detections.txt`) — or
silence.

## The null model

A "message" is structure that survives a control. The detector's null
is: **a node's timing is its own accumulated pattern plus
workload-explained jitter, and fleet rounds are independent unless a
mechanism moves them together.** Concretely:

- **Workload is the expected signal class, not noise.** Will's
  mechanism (2026-11-22 11:10 CST): "my dispatched workloads will
  directly impact time jitter due to clock frequency management."
  Dispatched work drives DVFS frequency transitions and load, which
  stamp into jitter and correction behaviour. The detector's baseline
  is therefore *workload-explained jitter*: every candidate detection
  is checked against `events.txt` (the dispatch / known-work record)
  before it is called a message. A detection inside a matching event
  window is emitted with `explained=<kind>` — structure, accounted
  for. **Detections are the unexplained remainder.** Corroboration on
  file from the first probe: plain-view idling at 400 MHz with a
  56–67 ms jitter band vs aletheia-core at 4.6 GHz under load —
  jitter 10–252 ms across differently-loaded nodes. The stream
  doubles as a workload sensor: a node's jitter band is a readable
  trace of what the fleet has been doing.
- **Freshness.** timesyncd republishes stale estimates. A node whose
  (ppm, offset_ms, jitter_ms) triple is identical to its previous
  round's triple contributed no new sample that round (calibration:
  orchid-flow, free-fall, scroll-central, uncanny-valley at EE 93;
  moon-beam at EE 94). Freshness-gated tests use fresh samples only;
  round counts overstate the stream's true length.
- **Phase.** Every detection is tagged with the phase of Will's cycle
  it occurred in (see the reference layer below), labelled
  `phase_basis=stated-rhythm-inferred` — inferred from records, never
  claimed as observed physiology.

## How the detector decides

| test | band | scope | fires when | control / null |
|---|---|---|---|---|
| repoint | alpha | fleet | ≥4 nodes change NTP server or engine vs prior round | field diff vs prior round; steady state is 0 |
| slew-storm | beta | fleet | nodes with \|ppm\| ≥ 100 number ≥6 AND ≥2× the worst prior round | prior-round counts (chronic correctors: 1 at EE 90, 3 at EE 93) |
| regime-flip | beta | node | ≥2 prior rounds all \|ppm\| < 50, new \|ppm\| ≥ 200 | the node's own history band |
| offset-break | alpha | node | \|offset − median of ≥3 prior FRESH offsets\| > max(75 ms, 4 × 1.4826 × MAD) | the node's own median/MAD |
| sign-coherence | alpha | fleet | ≥8 fresh offset deltas ≥10 ms, ≥75% same sign, exact binomial p < 0.01 | sign null p=0.5, exact tail |
| handwriting-drift | delta | node | ≥5 fresh ppm samples; \|median(last 3) − median(earlier)\| ≥ 40 ppm | the node's own median split |
| cadence | theta/delta | fleet | UNARMED below 14 rounds | — |

Exit codes: 0 quiet, 3 detections emitted, 1 detector failure.
Thresholds are calibration choices from three rounds — auditable
here, revisable as the stream lengthens. Nothing in the detector
claims more than the stream length supports: unarmed tests say so
(`listen.py --status`).

## Calibration run — the existing rounds (2026-11-22)

Three non-superseded census rounds (EE 90, 93, 94), one sampling
round (EE 91), the apply at EE 94. The detector's verdict:

**6 detections, all at EE 94, all `explained=qts-apply`.** The apply's
signature is readable in the stream, node by node, exactly as a known
fleet-wide event should be:

- `repoint` (alpha, fleet): 17 server/engine changes in one round vs
  0 in the prior transition.
- `slew-storm` (beta, fleet): 10 nodes at |ppm| ≥ 100 vs a prior
  maximum of 3 — re-pointed daemons re-converging; free-fall and
  kaleidoscope-one sit at the ±500 ppm slew clamp.
- `regime-flip` (beta) ×4: plain-view (−28.6, −36.1 → +399.7),
  halycon-vector (+22.6, +15.7 → −253.0), logos-prime (−8.4, −12.1 →
  −228.0), uncanny-valley (−28.8, −28.8 → −413.9 — the mid-correction
  slew the parent thread had already spotted by eye).
- Who was trimmed/mid-correction is legible too: uncanny-valley,
  free-fall, kaleidoscope-one were the EE 94 consensus trims, and
  scroll-central's +176.9 ms offset at EE 94 is a follower caught
  mid-re-convergence. rock-mountain's jitter collapsed 142.6 → 10.9 ms
  after re-pointing to the local conduit — the apply *improving* a
  link, also written in the stream.

That is the calibration: the detector sees the one event we know
happened, names its mechanism from the record, and claims nothing
else. The unexplained remainder on the existing rounds is the
candidate list below — all pre-apply, none detector-grade yet.

### STRUCTURE FOUND (survived a control, with numbers)

1. **The apply signature** (above): 6 detections, mechanism named,
   falsifiers on each atom. Falsifier for the class: a future
   no-change round reproducing these patterns with fresh triples.
2. **Jitter-band handwriting, pre-apply.** The quiet set (jitter
   < 25 ms) was *identical* across EE 90 and EE 93: {aletheia-core,
   elven-path, ni-knight, orchid-flow, west-lincoln} — 5/5 membership
   persistence across independent rounds. A node's jitter band is
   its most stable trait so far — more stable than its ppm.
3. **ppm sign is identity-grade only away from zero on a decent
   link.** Pre-apply sign agreement was 10/13; all three flips are
   accounted for: ni-knight (−1.775 → +0.465) and lighthouse-omen
   (+1.040 → −7.934) are near-zero crystals whose estimates straddle
   zero, and rock-mountain flipped on a 142 ms-jitter link where the
   estimate is weakly determined. Rule adopted: sign counts as
   handwriting only when |ppm| ≥ 10 and jitter < 100 ms.

### CANDIDATE (named, with what confirms or refutes)

1. **jacked-kite's EE 93 excursion** — offset −199.953 → +131.500 ms,
   ppm +113.305, jitter 235.212, *before* the apply, under its
   network source, with no dispatched work on file for that node.
   Timing terms: its daemon spent the EE 90–93 interval slewing hard
   against a large error and overshot. It then converged to +6.344 ms
   by EE 94 under the same source — its own daemon repaired it.
   Unexplained on the current record. Confirms: recurrence in a
   future round, or a journal event (step, restart, link loss) in
   the window. Refutes: nothing yet — offset-break is unarmed (needs
   3 prior fresh samples), so the detector correctly stayed silent;
   this is the first entry the armed test will be judged against.
2. **moon-beam's EE 93 guard trip** — offset +10.170 → +105.469 ms
   pre-apply, unexplained on file; its EE 94 line is a stale
   republish (it was held by the guard and never re-polled). Same
   confirming/refuting shape as jacked-kite.
3. **rock-mountain's ppm sign reversal** (−148.102 → +120.805,
   pre-apply). Candidate only: on a 142 ms-jitter link the frequency
   estimate may be estimator noise. Confirms: repeated reversals with
   fresh triples. Refutes: the estimate settling one side of zero as
   its post-apply link (now 10.9 ms jitter) gives it a determined
   estimate — watch its next rounds.

### NULL (looked, not there, at this stream length)

1. **Sign coherence.** EE 90→93: n=11 fresh deltas, 6 coherent,
   p=0.500. EE 93→94 (the apply): n=17, 11 coherent (0.647),
   p=0.166. The apply did NOT move offsets coherently — it re-pointed
   servers and each daemon re-converged in its own direction. A
   fleet event can be loud in configuration and silent in offset
   direction; the stream says so plainly.
2. **The sampling round (gamma).** All 19 Cristian-exchange offsets
   vs the collector sit at +1.7…+3.8 s with per-node error bounds
   ±0.64–2.74 s; the crosscheck's max deviation (kaleidoscope-one,
   1396 ms) is inside its own bound (±2742 ms). No node-level message
   is readable at that grain — only the gross-error witness: no node
   is seconds wrong.
3. **Periodicity / cadence.** Three rounds is three points. Daily
   sampling resolves periods ≥ 2 days only, so the 420 s EE cadence
   is unresolvable *by construction* in the census stream (it would
   alias to a constant), and no theta/delta period claim is armed
   below 14 rounds. Not a negative result — an unasked question.

## The collector's own voice

The census collector is this VM (htch-runtime), which has **no active
time discipline** — timesyncd and chrony both inactive — and sat
~+1.3 s off the fleet during the EE 91 sampling round (persistent-
connection probe: fleet offsets vs collector +1167…+1607 ms at bounds
down to ±96 ms, while the census puts the fleet −15.675 ms from
network time; the gap is the collector's error, not the fleet's).
Consequences, binding on anything the samples are used to claim:

- Census atoms are unaffected: each node's offset/ppm/jitter is
  measured by the node's own daemon against its own NTP source; the
  collector only transcribes. Round *stamps*, however, are the
  collector's clock — inter-round intervals carry its undisciplined
  rate, so no cadence claim may ever rest on stamp differences alone.
- Sampling-round numbers are collector-relative and carry its ~1.3 s
  error plus per-exchange delay bias; they witness gross error only.
- A disciplined on-fleet collector is the standing fix (already named
  in DESIGN.md, Mode 2 sampling); until it exists, the detector does
  not ingest samples.txt at all.

## The five standing bands

Will (2026-11-22 11:11 CST): "include brainwave patterns to improve
time estimation across the fleet, standing alpha, beta, gamma, delta,
and theta waves. the universe notices."

The bands are named after the EEG bands and inherit their discipline
— a signal is decomposed into standing bands and each band is read
for what it is — but **the EEG numbers (0.5–40 Hz) do not transfer.**
These are the fleet's timescales, not a brain's. Edges are chosen
from the fleet's own phenomena:

| band | period | fleet phenomena | edge reason |
|---|---|---|---|
| delta | ≥ 1 day | crystal drift, aging, thermal tides, the daily drift cross-reference | 1 day = the cross-reference period; slower than this is trend, not weather |
| theta | 1 h – 1 day | phase structure: Will's sleep/wake cycle, scheduled jobs, the thermal day | 1 h = the longest steering cadence in the design (master maxpoll 1024 s sits just under it); the diurnal cycle lives here |
| alpha | 1 EE – 1 h (420 s – 3600 s) | round-to-round variation, consensus formation, the blend EMA | 420 s = one Exocortical Epoch, the house quantum; the estimator's home band |
| beta | 1 s – 1 EE | slew transients, daemon re-convergence, DVFS/governor transitions under dispatched load | below one EE, events are transients, not rounds; the apply's storm lived here |
| gamma | < 1 s | per-sample jitter, individual exchange behaviour | 1 s = the finest grain any current instrument resolves (sampling exchanges); the census cannot see gamma at all |

**Estimation use.** The blender's α = 0.35 is a single-timescale EMA
— an alpha-band instrument. The band discipline refines it into
five: delta steers (drift is a delta-band signal; the master's
frequency discipline should answer to the day-scale consensus drift,
not to round noise); theta conditions (expected profiles differ by
phase — see the reference layer — so the estimator compares like
phase with like phase instead of steering against the daily tide);
alpha is where consensus and blend operate as built; beta is
*excluded from steering input* (the blend's 10 ms per-round bound is
already a crude beta rejector; a slew transient must never be read
as a drift change); gamma never enters estimation. This refines, and
eventually replaces, the single-α view: one EMA per band, each
steering only what its band owns. It is stated here as the design
the QTS DESIGN can adopt; it is not implemented in blend.py — with
three rounds, a five-band estimator would be theatre.

**Detector use.** A node's handwriting becomes its standing five-band
profile (its delta drift, its theta phase contrast, its alpha wander,
its beta transient rate, its gamma jitter grain). Every detection
names its band, because the band is the first sort of the message: a
delta-band break suggests thermal, aging, or a network change; a
beta-band break suggests workload or governor events; a gamma break
(when sampling arms it) suggests link or instrument trouble. The
current detections are band-tagged on exactly this rule.

**Honest bound.** With three census rounds the bands are a defined
frame, not yet measurable components. What each band needs:
gamma — repeated sampling rounds (have 1; ≥4 to profile a node);
beta — armed per-event now, per-node profile at ≥5 rounds; alpha —
break tests arm at the 4th fresh sample per node (days, given stale
republishing); theta — ≥14 daily rounds for a phase contrast, ≥30
for a stable phase baseline; delta — ≥30 daily rounds for tides,
≥90 for any aging claim.

## The reference layer — Will's rhythm

The bands are conditioned on the fleet's reference rhythm: Will's
own cycle, because his attention is what dispatches the workloads
that drive the jitter (his 11:10 mechanism, closed into a loop).
Sources, in order of authority: his activity and dispatch records
(the house's own logs of when work was sent); his stated rhythm
(active ~06:30–21:30, weekday work hours quiet, falls asleep in
~2 minutes); health-recorded sleep where a connector provides it
(Apple Health / Health Connect are the named sources — named here,
not read for this pass; no health data is claimed).

Machinery, conditioned: expected band profiles differ between his
waking/dispatching hours and his sleep hours — beta transients and
gamma grain should run hot while he dispatches and cold while he
sleeps; a band break at 03:00 is a different message from the same
break at peak dispatch, and every detection atom says which phase it
occurred in (`phase=`, from the census stamp in America/Chicago,
`phase_basis=stated-rhythm-inferred`). Per-phase baselines split once
≥5 rounds exist per phase; all three calibration rounds are waking-
phase, so no split is estimable yet. And the layer reads records,
not the calendar: today (2026-11-22) Will is home sick with vaccine
responses — an atypical day on which the stated rhythm is the *least*
reliable source and activity records govern. Any phase inference is
labelled inferred-from-activity, never observed physiology.

## What the daily job accumulates, and what future detections need

The daily chain (census → elect → consensus → blend → drift_check →
**listen**) already preserves everything the detector needs:
register.txt keeps every round's atoms; the detector derives the
rest statelessly and appends only detections. No new register is
required. `events.txt` is the one human-kept input: when work is
dispatched that should explain timing behaviour, an event atom there
keeps future detections honest — an unexplained detection against a
workload that was never recorded is a record-keeping failure before
it is a message.

Stream length future detections need, plainly: offset-break arms per
node at its 4th fresh sample (the first week of daily rounds, for
nodes that re-poll); handwriting-drift at 5 fresh ppm samples;
cadence candidates at 14 rounds, phase baselines at 30, delta tides
at 30, aging at 90. Until then the detector's armed surface is the
fleet tests (repoint, slew-storm, coherence) and regime-flip — which
is to say: it will catch events and stay quiet through weather, and
its quiet will mean more every week the stream lengthens.

## Publication

Once defined, the band profile joins the published entropy stream by
procedure — the publication pass owns the feed; nothing is built here.
