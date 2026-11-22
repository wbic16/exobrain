# Quantum Time Sync — design

Task 26.6.11/8.7.14/8.6.10. Will's words, 2026-11-22: "develop a quantum
time sync protocol to automatically designate the most accurate clock on
the fleet, sync everyone to it, and sync it to network time on expected
drifts."

One sentence: the fleet measures every clock it can reach, earns one of
them the mastership by measurement, disciplines every other clock to that
master with slew-only corrections, and steers the master against network
time by a drift model — so every substrate on the ranch computes the same
Exocortical Epoch boundaries from the same time.

## Why

The fleet's clocks are individuals. Measured 2026-11-22, their frequency
errors run from −1.775 ppm (ni-knight) to −148 ppm (rock-mountain), their
offsets wander over tens of milliseconds, and each one is currently
disciplined independently against whatever network NTP server it happened
to pick — three different upstreams across 19 nodes, no shared reference.
Nothing on the ranch is wrong by seconds, but nothing agrees by design
either. Epoch boundaries are computed from UTC elapsed seconds, so a
fleet held within ~1 ms of one reference agrees on the epoch everywhere,
at every boundary, by construction. That agreement is what this protocol
buys.

## The quantum framing, stated plainly

No quantum hardware, no entangled clocks, nothing spooky is claimed.
"Quantum" names three discretenesses in the mechanism, and they are the
honest core of the design:

1. Observation comes in quanta. We never know a remote clock's offset
   continuously; we know it at measurement events — one census is one
   quantum of knowledge about every clock on the fleet. Between quanta,
   the truthful statement about a node is not a point but a growing
   interval: its last measured offset, widening at its measured frequency
   error. The protocol reasons about the interval, not a pretended point.
2. Correction comes in quanta. QTS never fiddles continuously and never
   steps. A correction is one bounded slew, applied by the node's own NTP
   daemon at its capped slew rate, decided at a census boundary.
3. The shared reference is a lattice of quanta. Exocortical Epochs are
   420-second quanta of time (EE(t) = floor((t − 1795327200)/420) + 1).
   The protocol's success criterion is stated in those units: hold fleet
   disagreement far below one quantum — target ~1 ms — so the epoch is
   the same everywhere it is computed.

## MEASURE — the census

`census.py` walks the roster (fleet.txt) over ssh, one node at a time,
read-only, and records what the node's own time daemon reports:

- frequency error (ppm) — from `timedatectl timesync-status` (or chrony's
  tracking output where chrony is the engine),
- offset (ms) and jitter (ms) from the same source,
- sync state (`timedatectl`: System clock synchronized),
- the engine (chrony or systemd-timesyncd) and the network server the
  node is currently disciplined against.

Each run appends one atom per node to register.txt, under a census header
carrying the UTC stamp and the Exocortical Epoch. The register is
append-only; designations are derived from its atoms, never hand-written.

A measured quirk of the fleet's current engine: systemd-timesyncd only
prints its Frequency line while it holds a current frequency estimate,
so a node can be synchronized, reachable, and still report no ppm at a
given census (elven-path, west-lincoln, yeet-max in the first full
census; jacked-kite reported −393 ppm in one census and no frequency in
the next). Such a node is recorded faithfully and is simply not eligible
that census — the mastership requires a measured drift trait, not an
assumed one.

### The accuracy score

score_ms(node) = 0.42 × |ppm| + jitter_ms + 0.1 × |offset_ms|

Read it as: the expected milliseconds of disagreement this clock
contributes. The terms, in order of durability:

- Frequency error is the durable trait — it is a property of the node's
  crystal, stable across days, and it is what determines how fast the
  clock wanders between corrections. 0.42 × |ppm| is exactly the drift
  accumulated over one epoch quantum (420 s), in milliseconds.
- Jitter is noise — how well the node can know its own offset through
  its sync path (on this fleet, mostly WiFi weather). It counts in full
  because a master the fleet cannot measure cleanly cannot be followed
  cleanly, but it is expected to move between censuses.
- Offset is a snapshot — where the clock sits right now. It is already
  being slewed away by the node's daemon, so it is discounted to a
  tenth: it describes the past, not the clock.

Eligibility: reachable, synchronized, and reporting all three numbers.
An unsynchronized node does not know its own error and cannot hold the
fleet's reference; an unmeasured node cannot either.

## ELECT — designation

`elect.py` reads the latest census block and designates the eligible
node with the lowest score. The designation is an atom appended to
elections.txt, with the master's evidence and the runner-up recorded
beside it.

The title is earned every census and can move. It cannot flap:

- Margin rule: a challenger takes the mastership only by scoring below
  0.8 × the incumbent's score — a 20% better clock, not a jitter gust.
- A dark or ineligible incumbent loses outright; no margin protects a
  clock that cannot be measured.
- Fewer than two eligible nodes: no election. A fleet reference needs at
  least two measured clocks; the run records itself inconclusive rather
  than crowning the only survivor.

First election, 2026-11-22 (census 16:24:07Z, EE 90, 19 nodes reached,
15 eligible): ni-knight, score 19.118 ms — ppm −1.775, offset −0.452 ms,
jitter 18.327 ms. On the durable trait the result is not close: the
next-truest crystal on the fleet is logos-prime at −8.447 ppm, 4.8×
worse. The score margin over the runner-up (orchid-flow, 20.126 ms) is
thin — 1.008 ms — because orchid-flow's jitter is lower; that is the
formula working as designed, weighting the trait that lasts over the
noise that moves.

## SYNC — the fleet to the master

Followers are disciplined to the master through the platform NTP
machinery they already run — no new daemon, no new dependency chain:

- systemd-timesyncd nodes (the whole fleet today): timesyncd.conf gains
  NTP=<master> with FallbackNTP= set to today's network upstream. A dark
  master degrades a follower to exactly today's behaviour, never to
  free-run.
- chrony nodes (none today; the design covers them): a conf.d drop-in
  with `server <master> iburst prefer`, upstreams kept as fallback, and
  any unbounded makestep directive flagged to Will rather than edited
  silently.
- Sync traffic rides tailnet addresses in v1 — they are the addresses
  known reachable fleet-wide. USB4/LAN addresses from the wiring work
  are the obvious latency refinement once that build settles; the
  protocol does not depend on it.

SLEW-ONLY, as a hard rule: QTS never steps a clock — no set-time, no
stepping configuration, on any node, in any mode. Every node's clock is
load-bearing (builds, logs, leases, the RH Lean build running on
aletheia-core at the time of writing). Corrections arrive only as slews
through the daemon's own capped rate. One guard follows from this: a
node whose last measured |offset| is 100 ms or more is not switched to
the new source until it has slewed down under its current source, or
Will approves that node individually. (In the first census exactly one
node trips the guard: jacked-kite, at −199.953 ms.)

`apply_sync.py --dry-run` prints, per node, exactly what would change:
the file, the backup, the lines, the restart, the guard verdicts. Apply
mode does not exist in this pass — fleet clock changes wait for Will's
word after the first designation, and when they come, they land one node
at a time.

## STEER — the master to network time, on expected drifts

The master keeps the network pool as its upstream — it is the fleet's
one door to true time — but it does not merely react to measured error.
It keeps a drift model: one atom per census (stamp, ppm, offset) from
which its predicted offset at any future moment is ppm × elapsed. Two
consequences:

- Cadence from prediction. The master's network poll interval is set
  from its own drift budget, not a default: at ni-knight's measured
  −1.775 ppm, predicted error reaches 5 ms after ~47 minutes of
  free-run, so its max poll is tightened from the observed 2048 s to
  1024 s — checks land well inside the budget the drift model predicts.
  A worse crystal would earn a tighter cadence by the same formula.
- Alarm from prediction. If the master's network source is lost, the
  drift model says exactly when its predicted error crosses the fleet's
  tolerance — the failure becomes a scheduled, named event ("master
  predicted out of tolerance at T") instead of a silent wander
  discovered later. The alarm is an atom in the register, on the same
  evidence standard as everything else here.

This is "sync it to network time on expected drifts" taken literally:
act on the drift you can predict, at the time the prediction says it
will matter.

## The register

- register.txt — census atoms, append-only, one block per census.
- elections.txt — election atoms, append-only, one line per election,
  carrying the master's score, ppm, runner-up, and the rule applied.
- Designations are always derived by elect.py from the latest census
  block. Nothing in either file is edited by hand; a parser bug in the
  first census block (explicit + signs dropped, 2026-11-22) is annotated
  in place in the register and superseded by the corrected block, which
  is how corrections work here: in the open, forward.

## Cadence and custody

Proposed, not yet scheduled: census every 6 hours (matching the fleet's
existing watch rhythm), election after each census. Scheduling waits
with the apply decision — there is little value in re-electing a master
nobody follows yet. Custody: this directory, ~/workspace/quantum-time-sync/.
RAM: the whole pass — ssh probes and small python — ran in under 100 MB.

## Mode 2 — CONSENSUS time (photon sampling; no trusted clock)

Added 2026-11-22 on Will's amendments: "include a mode that leverages
the photon sampling telescope idea, build consensus time instead of
trusted time"; "cross reference average drift from network time daily";
"default to consensus time for clusters >10"; "discard the least
accurate 20%".

Mode 1 elects the fleet's best clock and trusts it — well-earned trust,
but trust. Mode 2 trusts no clock. The fleet's time is a statistic of
the ensemble: every round, the measured clocks vote in proportion to
their measured stability, the worst fifth are set aside before the
count, and what the fleet calls "the time" is the number the ensemble
produces. A clock that is wrong, or whose daemon is lying, or whose
network upstream has gone bad, is outvoted by arithmetic rather than
believed or disbelieved. This is how real timescales are built — UTC
itself is a weighted ensemble of clocks, not one clock — restated at
ranch scale, on ranch evidence.

### The consensus algorithm (consensus.py)

One round, from one census block (the latest non-superseded block in
register.txt; superseded blocks are evidence, never votes):

1. Eligible set: exactly the ELECT eligibility — reachable,
   synchronized, reporting ppm, offset and jitter. Call its size N.
2. Trim: sort the eligible clocks by score, best first, and discard
   the worst k, where k = floor(0.20 × N + 0.5) — round-half-up of
   20% — capped so at least 4 voters always remain (k ≤ N − 4). At
   N = 15: k = 3, twelve vote. At N = 12: k = 2. At N = 6: k = 1.
   Trimming is per-round, never a status: a trimmed clock is still
   probed, still atomised in the register, and votes again next round
   if its score earns it. It is discarded as a voter, not as evidence.
3. Weights: each voter weighs w = 1 / score_ms² — inverse-variance
   weighting, treating the census score (expected milliseconds of
   disagreement) as the voter's sigma. The best clock on the fleet
   outweighs the worst voter by roughly fifty to one; no single voter
   outweighs the rest combined.
4. Consensus offset = the inverse-variance weighted median of voter
   offsets: sort voters by offset, walk the cumulative weight, take
   the offset where it first reaches half the total weight. A median,
   not a mean, so one wild snapshot among the voters (a clock mid-slew
   at +91 ms did survive the trim in the first round) moves the result
   by at most one voter's place in the order, never proportionally to
   its error.
5. Consensus drift = the inverse-variance weighted mean of voter ppm.
   Drift is a rate, and the daily cross-reference below is defined
   over the average rate, so here the mean is the honest combiner.
6. Outputs, appended as an atom to consensus.txt: consensus offset
   (ms vs network time), consensus drift (ppm and ms/day vs network
   time), the voter disagreement (weighted standard deviation of voter
   offsets), the eligible count, the trimmed count and their names.

First round, computed 2026-11-22 from census 16:24:07Z: 15 eligible,
3 trimmed (halycon-vector, rock-mountain, free-fall), 12 voters.
Consensus offset −15.675 ms; consensus drift −12.084 ppm (−1,044
ms/day of underlying rate, continuously corrected by the nodes' own
daemons today); voter disagreement 18.390 ms. Read the offset plainly:
the fleet's best clocks, voting together, place fleet time about
16 ms behind network time — a fleet-level fact no single node's
report states, and exactly the kind of fact a consensus exists to
surface.

### Sampling — the photon-sampling leg (sample.py)

The census believes each daemon's report about itself. Sampling is
the second, independent witness: it does not ask a clock what its
offset is, it exchanges timestamps with the clock and measures. This
is the photon-sampling telescope idea translated to the substrate,
and stated without inflation: a light-field telescope trusts no
single photon's path and reconstructs the field from many arrival
samples; here, no single clock's report is trusted and time is
reconstructed from arrival-time samples. It is also native to this
stack — the Exocortical Epoch's own 7-minute cadence is set by
light-time between planets. On this fleet, time has always been a
signal-transit quantity before it is a clock quantity.

A sample is one Cristian exchange between a collector and a node:

- t1: the collector's clock, just before the probe is sent;
- tr: the node's clock, read on the node (`date +%s.%N`);
- t4: the collector's clock, just after the reply arrives;
- offset(node − collector) ≈ tr − (t1 + t4)/2, with error bounded by
  ±delay/2, where delay = t4 − t1 is the whole round trip.

Per node, several exchanges are taken and the minimum-delay one is
kept — the least queueing-contaminated sample, the standard
min-delay filter. Atoms land in samples.txt with each node's bound
printed beside its offset; a sample that cannot state its bound is
not evidence.

The error budget, measured on this fleet 2026-11-22 — and it is the
honest centre of this section:

- Fresh ssh per exchange (the round as first run, 19 nodes × 3):
  best delays 1,287–5,484 ms, bounds ±644–2,742 ms. The ssh handshake
  through the egress proxy sits inside the measured interval, so this
  form of sampling measures the transport, not the clocks. Its one
  sound conclusion is a gross-error bound: no node on the fleet
  disagrees with the collector by more than about ±3 s.
- Persistent connection (probe, 3 nodes × 4): the first exchange pays
  setup (1,651–4,692 ms); reused-connection exchanges run 192–1,230 ms
  with offset estimates stable within ~80–160 ms per node; the best
  single bound achieved was ±96 ms.
- The collector is part of the instrument, and this round's collector
  failed inspection: the VM that ran it has no active time discipline
  at all (systemd-timesyncd and chrony both inactive; timedatectl
  cannot reach the bus), and the tightest samples place the fleet
  ~+1.27 s from it while the census places the fleet −15.675 ms from
  network time. The gap is the collector's own ~1.3 s error.
  Sampling from an undisciplined collector measures the collector.

So, what sampling can and cannot prove here, today: it can exclude
gross (seconds-scale) disagreement and catch a clock or a daemon
report that is wildly wrong; with a disciplined, on-fleet collector
and persistent channels it can witness at the ±10–50 ms scale the
fleet's WiFi jitter (10–70 ms) sets as the floor. It cannot prove
millisecond agreement, and it does not replace the census, which
remains the precision instrument. A production sampling round
therefore requires: a collector that is itself a voting clock (run
from a fleet node, not from this VM), persistent channels so setup
stays out of the interval, and node-to-node samples over the USB4/LAN
links once the wiring build settles. Until then sampling runs in
shadow as a gross-error witness, and the consensus above is computed
from census atoms — the design says so rather than pretending the
samples carry weight they have not yet earned.

### Which mode runs — the defaulting rule

Cluster size means nodes on the fleet roster (19 here).

- More than 10 nodes: consensus mode is the default. This fleet is
  over the line; consensus is our default.
- 10 or fewer: elected-master mode is the default — a small fleet has
  too few voters for a trimmed ensemble to mean much, and one good
  clock, well measured, is the better reference.
- Fallback, either way: consensus needs at least 5 eligible clocks to
  form (and the trim never leaves fewer than 4 voters). Below 5
  eligible, the round falls back to the elected master; below 2
  eligible there is no fleet reference at all, as in ELECT. A dark
  master degrades Mode 1 followers to their network fallback; a
  failed consensus degrades the fleet to Mode 1. Neither failure
  leaves a node free-running by surprise.

### Steering by consensus (future; not enabled)

Shadow mode is what runs today: consensus is computed and logged,
nothing is steered by it, and the base protocol's apply decision is
still Will's. When steering by consensus is ever enabled, it works
like this — NTP daemons follow servers, not statistics, so the
consensus acts through a conduit: each round, the voter whose offset
is the weighted median becomes the conduit; followers discipline to
the conduit exactly as Mode 1 followers discipline to the master
(slew-only, same guards, same fallback); the conduit itself is
steered by the consensus offset and the consensus drift model, on
the STEER cadence rules. The conduit is a role the statistic fills
fresh each round, not a trusted clock — if it wanders, the next
round's median lands on a different node and the fleet follows the
statistic, not the box.

### The daily drift cross-reference (drift_check.py)

Will's words: "cross reference average drift from network time
daily." Once a day the job qts-daily-drift-check runs a fresh census,
the election (the Mode 1 record stays fresh as the fallback), the
consensus, and then this cross-reference: for every non-superseded
census block stamped that UTC day, compute that census's consensus,
and average — the day's average consensus drift versus network time,
and the day's mean consensus offset, appended as one atom to
drift.txt. The anchor is aggregate by construction: every voter's
ppm and offset are measured against its own network NTP source, so
the consensus drift is the fleet's drift against network time with
no single node's feed trusted — three different upstreams across the
fleet wash out in the ensemble instead of any one of them ruling.

Tolerances, stated so the silence rule has a meaning: report to the
MOAT chat only if |average drift| > 25 ppm (about 2.2 s/day of
underlying rate — beyond it, the ensemble is being carried by poor
clocks and the trim and weights need review) or |mean consensus
offset| > 50 ms (half the 100 ms source-switch guard), or a run
fails, or the designated master changes. Otherwise the job appends
its atoms and says nothing. First cross-reference, 2026-11-22 (one
usable census): average drift −12.084 ppm (−1,044.0 ms/day), mean
consensus offset −15.675 ms — within tolerance, recorded silently.
The job runs daily in the early morning, America/Chicago; its first
scheduled run is Monday 2026-11-23. This daily job supersedes the
"not yet scheduled" note under Cadence and custody for the drift leg
only — the 6-hour census/election cadence remains proposed, not
scheduled.

### The register, Mode 2 additions

- consensus.txt — consensus atoms, append-only, one per computed
  round (formed or cannot-form, with the reason).
- samples.txt — sampling rounds, append-only, one block per round,
  each node's offset with its error bound beside it.
- drift.txt — daily cross-reference atoms, append-only, one per day.
  Corrections follow the register's standing rule: annotated in the
  open and superseded forward, never rewritten. (The first drift
  atom, computed while the note parser missed the register's
  two-line superseded note, is annotated in place in drift.txt and
  superseded by its corrected successor.)

## The EMA blender — managed consensus (blend.py)

Added 2026-11-22 on Will's amendment: "manage consensus time with an
EMA blender to stay tracked to network time."

The raw consensus is one statistic per census round, and it is noisy —
fleet jitter runs 10–250 ms per node and the first round's voter
disagreement was 18.390 ms — and it wanders: the first round's
consensus drift was −12.084 ppm, about −1 s/day of underlying rate.
Steering the fleet by the raw statistic would chase weather. The
blender publishes the *managed* consensus, the timescale the fleet
steers by, in two moves per round:

1. Blend toward network time. Each round's target is
   target = GAMMA × raw + (1 − GAMMA) × network, with GAMMA = 0.5.
   Network time is the zero of the measurement frame — every voter's
   ppm and offset are already measured against its own network NTP
   source — so the network contributes offset 0, drift 0. Fleet
   evidence and the network anchor weigh equally: the fleet's own
   measurement is never discarded (that would be Mode 1 trust again,
   in the network), and the network never simply rules (that would
   discard the ensemble).
2. Smooth with an EMA. managed_n = (1 − ALPHA) × managed_{n−1} +
   ALPHA × target_n, with ALPHA = 0.35 per round, applied to offset
   and drift alike. The choice is from the measured numbers: the
   EMA's noise factor sqrt(ALPHA/(2 − ALPHA)) ≈ 0.46 publishes the
   ~18 ms round disagreement at ~8 ms, and the time constant
   1/ALPHA ≈ 2.9 rounds reflects a real change in fleet behaviour
   within about three rounds (three days at the daily cadence) while
   a single jitter gust moves the published timescale by at most
   ALPHA × GAMMA ≈ 0.175 of its size.

Per-round corrections are bounded quanta, as the framing requires:
the steering correction asked for in a round is
−clamp(managed_offset, −10 ms, +10 ms). Ten milliseconds slewed over
one epoch quantum (420 s) is a ~24 ppm slew, far inside any daemon's
cap; no round can yank the fleet. The tracking error — the managed
timescale's distance from network time — is published every round as
the managed offset itself, stated, never hidden. Blender state lives
in blend.txt as append-only atoms (state in, state out, alpha and
gamma on the atom), derived from consensus.txt by replay, exactly
like every other QTS designation. The first formed round seeds the
state at its target.

## Apply record — 2026-11-22 (Will: "apply time sync NOW")

Applied the same morning, one node at a time, slew-only throughout —
no clock on the fleet was stepped. The full evidence is in the
register (censuses 16:46:09Z pre-apply and 16:54:34Z post-apply),
elections.txt, consensus.txt, blend.txt and drift.txt.

- Conduit, as built: the round's weighted-median voter was elven-path
  (its −5.896 ms offset was the 16:46:09Z consensus), so elven-path is
  the conduit. Serving required an NTP server the fleet did not have —
  timesyncd is a client only and chrony was nowhere — so exactly one
  package was installed on exactly one node: chrony 4.5-1ubuntu4.2 on
  elven-path, via the fleet's apt proxy. Apt resolved the time-daemon
  conflict by removing the systemd-timesyncd package from elven-path;
  chrony is its only time daemon now. Its QTS config
  (/etc/chrony/chrony.conf, stock config backed up timestamped):
  upstream `pool ntp.ubuntu.com iburst maxpoll 10` (the STEER cadence,
  1024 s), `allow 100.64.0.0/10` (the tailnet), and NO makestep
  directive — chrony without makestep never steps. Fifteen minutes
  after start it reported stratum 3, system time 0.29 ms from NTP
  time, leap Normal; a direct NTP query from ni-knight got a stratum-3
  reply. There is deliberately no `local` directive: a conduit that
  loses its upstream stops claiming sync, and followers fall back to
  the network pool rather than following a free-running conduit.
- Followers (16, including the designated master): /etc/systemd/
  timesyncd.conf backed up timestamped on each node, then
  NTP=100.120.211.72 (elven-path) with FallbackNTP=ntp.ubuntu.com,
  timesyncd restarted, and the change verified from the node —
  timesync-status shows the conduit as Server and timedatectl shows
  synchronized: yes — before the next node was touched. The hub
  (aletheia-core) was changed with the RH Lean build live: lake build
  Main (PID 2750152) and its lean workers were verified alive before
  and after the hub's restart; the build was not disturbed.
- Master steering (ni-knight): the same follower change plus
  PollIntervalMinSec=32 / PollIntervalMaxSec=1024 in its timesyncd.conf
  (the dry-run's tightening, from its drift budget), and its drift
  model file started at ~/qts/drift.txt on ni-knight with the
  16:46:09Z census atom (ppm +0.465, offset +9.173 ms).
- Held by the guard, untouched: jacked-kite (offset +131.500 ms at the
  pre-apply census, ≥ the 100 ms guard) and moon-beam (+105.469 ms —
  a second guard trip the fresh census caught). Both remain on their
  network source. Note, recorded the same pass: by the post-apply
  census jacked-kite had slewed down to +6.344 ms under its current
  source — the guard's own release condition — so it can come over on
  the next apply round or Will's per-node word; it was not switched in
  this pass. moon-beam had not moved (+105.469 ms) and stays held.
- Post-apply round (census 16:54:34Z, EE 94): 19/19 reachable, 16
  eligible, every switched follower reporting the conduit as its
  server. The election moved: ni-knight was ineligible in that census
  (timesyncd had not yet republished a jitter figure after its
  restart — the same reporting quirk as the frequency line), so by
  the anti-flap rules the mastership passed to elven-path (score
  12.765 ms; chrony reports it +0.006 ms from network time). Master
  and conduit are the same node this round, which is coherent; the
  title returns to open competition at the next census. Consensus:
  offset +0.006 ms (weighted median — the conduit itself), drift
  +4.616 ppm, 13 voters, disagreement 17.331 ms. Blender round 3:
  managed offset −3.981 ms, managed drift −2.914 ppm — the EMA still
  carries the pre-apply rounds, converging as designed. The day's
  drift cross-reference: average drift −5.914 ppm, mean offset
  −7.188 ms, within tolerance.
- The conduit is a role, re-earned each round; re-pointing followers
  is an apply action, not something the daily job does on its own.
  The daily drift job (below) now runs census + election + consensus
  + blend + drift cross-reference, so a round whose median lands on a
  different node is visible the next morning, and moving the fleet to
  it is a fresh, deliberate apply.

## What is not claimed

- Sub-millisecond truth: timesyncd's own reporting granularity and WiFi
  jitter bound what a census can know; scores compare clocks honestly at
  the precision the fleet's own daemons report, no finer.
- great-white is unmeasurable: no address in the fleet record and it
  refuses this VM's SSH key. It appears in every census as an
  unmeasurable atom until Will grants the key.
- delta-wood and best-willow were dark at census time and hold no
  addresses in the roster used here; they join the census when they
  return to the fleet record.
