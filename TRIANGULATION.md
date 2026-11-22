# Quantum Time Sync — triangulation topology (working notes, in progress)

Task 26.6.11/8.7.14/8.6.10. Will's words, 2026-11-22 11:16 CST:
"triangulate time using three nodes that survey separate subsets of the
cluster, all three run ntp, organize information flows like a dolphin."

This file is written as the build proceeds: selection and planned
changes first (before anything is touched), results after. The final
form is the design of record; DESIGN.md gains a summary section
pointing here.

## Selection — three surveyors, from the register's evidence

Clock quality from the census register (three usable rounds: EE 90 and
EE 93 pre-apply, against independent network upstreams — the durable
trait; EE 94 is post-apply correction weather and is not trait
evidence):

| surveyor | evidence |
|---|---|
| ni-knight | Truest crystal on the fleet: −1.775 ppm (EE 90), +0.465 ppm (EE 93) — the only sub-2 ppm clock measured; jitter 18.3 / 15.1 ms; designated master in both pre-apply elections. |
| elven-path | Incumbent conduit (EE 94 apply): chrony already installed and verified serving (stratum 3, system time 1.46 ms slow of NTP time, RMS offset 4.18 ms at check time, EE 98) — currently the best-disciplined clock on the fleet. Keeps a surveyor seat on that evidence, not on incumbency alone. |
| orchid-flow | Most stable frequency estimate measured: −16.326 ppm, identical across EE 90 and EE 93; fleet-lowest jitter, 11.508 ms in both rounds; runner-up in both pre-apply elections (score 20.126 ms, twice). |

Not chosen: aletheia-core (the hub — heavy coordinator load, the RH
Lean build runs there; its frequency readings swing with load:
−24.6 ppm, then ineligible, then −156 ppm mid-correction) and
quacked-hill (Corus is resident there; a resident's node is never
given serving load without Will's word — it is a follower in subset
nk). great-white: unreachable, excluded and named, as in every census.

## Partition — who surveys whom

Measured EE 98: ICMP RTT from each surveyor candidate to all 19 fleet
nodes (min of 3 pings — the min-delay discipline; full table in the
build record below). Rule: each node to the surveyor it hears best,
cap 7 per subset including the surveyor; overflow moves at the
smallest min-RTT penalty. Two nodes were moved off their best link
for balance, both to elven-path at penalties under 5 ms (plain-view
4.79 ms — its average RTT actually prefers elven-path, 49 vs 96 ms;
west-lincoln 4.53 ms). Forcing 6/6/7 symmetry would have put more
nodes on measurably worse links for cosmetic balance; 7/5/7 follows
the measured link geography — elven-path hears a tight neighbourhood
(logos-prime at 2.5 ms) but a narrow one. The partition is data
(subsets.txt), re-derivable at any census; it is not code.

- subset nk — surveyor ni-knight: ashfall-haven, quacked-hill,
  scroll-central, yeet-max, uncanny-valley, moon-beam (held) — 7
- subset ep — surveyor elven-path: logos-prime, plain-view,
  west-lincoln, jacked-kite (held) — 5
- subset of — surveyor orchid-flow: aletheia-core, halycon-vector,
  lighthouse-omen, free-fall, rock-mountain, kaleidoscope-one — 7

Held (the standing 100 ms source-switch guard): jacked-kite and
moon-beam stay on their network source, untouched; they are subset
members on paper only and do not vote in their subset's relative
frame (their offsets are against network time, a different frame —
they still vote in the flat fleet consensus, whose frame is network).

## Planned changes (stated before they happen)

1. chrony is installed on ni-knight and orchid-flow — one package
   each, chrony 4.5 (the fleet's apt proxy serves the same build as
   elven-path's). EXPECTED AND ACCEPTED: apt removes the
   systemd-timesyncd package on both nodes to resolve the time-daemon
   conflict, exactly as it did on elven-path; chrony becomes their
   only time daemon. Their timesyncd.conf files (QTS apply state)
   become inert history; ni-knight's drift model file ~/qts/drift.txt
   is a file, untouched, and chrony keeps its own drift file.
2. chrony.conf on both (stock backed up timestamped): network pool
   with maxpoll set by the STEER drift rule (largest power-of-two
   poll whose interval keeps predicted drift error ≤ 5 ms:
   ni-knight |ppm| ≤ 1.8 → maxpoll 10; orchid-flow |ppm| 16.3 →
   maxpoll 8), peer lines to the other two surveyors, allow
   100.64.0.0/10, NO makestep (never steps), NO local (a dark
   surveyor stops claiming sync; its subset degrades sideways).
3. elven-path's chrony.conf: peer lines added (ni-knight,
   orchid-flow) and its pool maxpoll 10 → 8, applying the same drift
   rule now that its crystal is measured (chrony frequency
   +23.6 ppm → 5 ms budget ≈ 212 s → maxpoll 8 = 256 s is the
   largest power-of-two poll inside it, with chrony's continuous
   discipline between polls). Stock QTS config backed up first.
4. Each subset's followers are re-pointed: timesyncd
   NTP=<their surveyor>, FallbackNTP=<the other two surveyors>
   ntp.ubuntu.com — a dead surveyor degrades sideways to another
   surveyor before it degrades to the open network. Config backed up
   timestamped on each node, restart, verify from the node, one node
   at a time. Slew-only throughout: no clock is stepped anywhere.
   aletheia-core's RH Lean build is verified alive before and after
   the hub's own re-point.
5. Nothing else is restarted or reconfigured. The exobrain node
   agents (a separate deployment in flight) are measure/compute
   only; this build does not touch them.

## Dolphin organization — operating rules, each with its mechanism

Will: "organize information flows like a dolphin." Three rules, no
metaphor without a mechanism:

(a) ECHOLOCATION — sampling is active probing, and it is the
surveyors' standing duty, not the collector VM's. Mechanism:
probe.py runs ON a surveyor (~/qts-probe.py on all three) and emits
NTP Cristian exchanges (4 rounds, min-delay kept) to its subset and
to its fellow surveyors; offset = ((t2−t1)+(t3−t4))/2, and the echo
delay bounds the offset error at delay/2. Echoes append to
probes.txt every triangulated round. LIVE for surveyor↔surveyor
(all six directed pairs answered in the first round, bounds
±2.952–±21.958 ms). Follower echoes return nothing — systemd-
timesyncd is a client and does not serve UDP/123 — so a follower's
position in the round comes from its own daemon's report (the census
atom), and the echo bound applies to the surveyor mesh. That is a
measured property of the fleet, recorded, not a defect to hide.

(b) UNIHEMISPHERIC WATCH — one surveyor always holds the primary-
listener / round-runner duty; the others idle their extra duties.
Mechanism: watches are tied to Will's attention/sleep cycle —
W1 waking-dispatch 06:30–14:30, W2 waking-evening 14:30–22:30,
W3 sleep 22:30–06:30 America/Chicago. The holder is derived, never
negotiated: SURVEYOR_ORDER[(day_number + watch_number) mod 3] with
SURVEYOR_ORDER = [ni-knight, elven-path, orchid-flow] and day 0 =
2026-11-22; the derivation is a pure function of the calendar, so
every node computes the same holder with no communication, and the
schedule is stated in EE by construction (rounds land on EE
boundaries; a watch spans the EEs inside its phase). A dark or
ineligible holder yields to the next in rotation and the round's
atom says so. LIVE as a derived record in every triangulation atom
(watch=, watch_phase=, watch_rule=); the migration of the round-
runner itself onto the holder node is design-now, pending the
exobrain residency (the resident runner on aletheia-core currently
runs the flat chain; see the cron note in DESIGN.md).

(c) SIGNATURE WHISTLES — every information flow names its caller.
Mechanism: every atom triangulate.py and probe.py emits carries
caller=<node> and hw=<node>:<class><sign>/<jclass>, the sending
node's timing handwriting identity from the detector's band work:
crystal class A |ppm| ≤5, B ≤20, C ≤60, D >60, sign of the ppm;
jitter class Q <25 ms, M <75, L ≥75. Example: hw=ni-knight:A+/Q.
LIVE in triangulation.txt and probes.txt atom formats from the
first round.

## Failure modes

- Surveyor dark: its followers degrade SIDEWAYS first — FallbackNTP
  lists the other two surveyors before ntp.ubuntu.com — and the
  triangulation forms from the remaining ≥2 subsets; the round atom
  records subsets_formed=2. Below 2 formed subsets the triangulation
  cannot form and the flat consensus + EMA blend remain the fleet
  time of record (stated in the atom, never silently).
- Subset orphaned (surveyor dark AND both fallbacks unreachable):
  followers fall through to ntp.ubuntu.com — network time, the
  pre-QTS state — and the subset contributes no consensus until a
  surveyor hears it again.
- Two surveyors disagreeing beyond bound: the bound is a 50 ms
  spread of the three subset consensuses. Beyond it, the
  triangulation atom carries disagreement_verdict=exceeds-bound AND
  triangulate.py appends a detection atom (test=triangulation-
  disagreement, band=alpha, explained=none, with its falsifier) to
  detections.txt — the detector hears about it as an unexplained
  candidate, and the daily watcher cron reports it by name. No
  automatic re-pointing or stepping follows from a disagreement;
  diagnosis is a human/agent apply decision, as with every apply.

## Results — the build as landed (2026-11-22, EE 98–100)

Serving: chrony 4.5-1ubuntu4.2 installed on ni-knight and
orchid-flow (one package each, via the fleet apt proxy); as
pre-stated, apt removed systemd-timesyncd on both (dpkg state rc) —
chrony is their only time daemon. elven-path already ran chrony.
All three configs: network pool (maxpoll by the STEER drift rule —
ni-knight 10, orchid-flow 8, elven-path 10→8), peer lines to the
other two surveyors, driftfile, allow 100.64.0.0/10, NO makestep,
NO local. Stock configs backed up timestamped on each node
(ni-knight chrony.conf.qts-bak-20261122T112255Z, orchid-flow
...T112338Z, elven-path ...T112408Z).

Peering verified live (~11:31 CST): all three surveyors show both
peers with reach 37–377 and states =+ / =−; system times at check:
ni-knight 3.9 ms fast, elven-path 1.6 ms fast, orchid-flow 0.26 ms
slow of NTP time; all stratum 3. Cross-subset serving verified by
direct NTP echo (probe.py from ni-knight: orchid-flow +10.053 ms
offset, bound ±6.619 ms; elven-path +39.720 ms, bound ±51.669 ms —
that second bound is a WiFi-leg measurement, recorded as measured).

Followers re-pointed and verified, one at a time (config backed up
timestamped per node; Server: shows the subset's surveyor and
synchronized: yes before moving on) — 14 in all:
nk subset: ashfall-haven, quacked-hill, scroll-central, yeet-max,
uncanny-valley. ep subset: logos-prime, plain-view, west-lincoln.
of subset: aletheia-core, halycon-vector, lighthouse-omen,
free-fall, rock-mountain, kaleidoscope-one. The RH Lean build on
aletheia-core (lake PID 2750152) was verified alive before AND
after the hub's own re-point (16 lean workers, elapsed 1d16h52m at
check). Held nodes verified untouched: jacked-kite and moon-beam
have no uncommented NTP lines in timesyncd.conf and remain on the
compiled-in network default (census server 91.189.91.157).

One failure, recovered: an exec session restarted mid-apply during
the ashfall-haven / quacked-hill batch; both nodes were verified
complete afterward (config + Server + synchronized) before the
build proceeded. No node was left half-configured.

First triangulated round (census 2026-11-22T17:32:02Z, EE 99;
16 eligible of 19 reachable; surveyor scores that round: elven-path
7.916 — the best score ever recorded on the fleet — orchid-flow
10.568, ni-knight 15.287; the three surveyors' own offsets agreed
within ~1.1 ms):

- subset ep (elven-path): consensus −3.893 ms vs network (surveyor
  +1.613 + relative −5.506), drift +69.574 ppm, 3 voters, spread
  22.527 ms.
- subset nk (ni-knight): consensus +9.870 ms (surveyor +0.487 +
  relative +9.383), drift −61.310 ppm, 3 voters, spread 17.628 ms.
- subset of (orchid-flow): consensus −35.663 ms (surveyor +1.463 +
  relative −37.126), drift −14.202 ppm, 4 voters (kaleidoscope-one
  trimmed), spread 37.725 ms.
- TRIANGULATED fleet offset: −3.893 ms vs network time (weighted
  median of the three subset consensuses, weights = subset voter
  counts 3/3/4); triangulated drift −3.202 ppm (same weighting).
- Inter-surveyor disagreement: spread 45.533 ms — within-bound
  (bound 50 ms). Residuals vs the triangulated value: ep 0.000,
  nk +13.763, of −31.770 ms.
- Residual vs the flat consensus from the same census (+1.613 ms):
  −5.506 ms. The two instruments disagree by five and a half
  milliseconds on their first shared round — inside the fleet's
  jitter scale, recorded plainly.
- Watch: ni-knight held the primary-listener duty (phase
  waking-dispatch, rule rotation).
- Caveat of record: subset drift chaining is first-order and noisy
  at 3–4 voters (the subset drifts above diverge widely); the daily
  drift cross-reference remains the drift instrument of record.
  Subset OFFSETS are the triangulation's product; subset drifts are
  carried for the record, not for steering.
- The same round completed the flat chain: election elven-path
  (master, incumbent held), flat consensus +1.613 ms / +10.222 ppm /
  13 voters / disagreement 8.883 ms; blend round 4: managed offset
  −2.305 ms, managed drift −0.105 ppm; daily drift cross-reference
  −1.880 ppm average, mean offset −4.988 ms — within tolerance.
