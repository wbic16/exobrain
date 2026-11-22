# exobrain — the fleet's time machinations

exobrain is the home of record for the time machinery of Will
Bickford's ranch fleet: the **Quantum Time Sync** protocol, consensus
time, the EMA blender, the time-stream detector with its brainwave-band
layer, and the published entropy stream. It runs on every fleet node.

**Provenance.** Built at Will's direction, 2026-11-22. Governing tasks:
26.6.11/8.7.14/8.6.10 (Quantum Time Sync), 25.7.10/9.8.14/7.6.9 (the
time-stream detector and the entropy publication), 31.8.10/9.8.14/8.8.10
(this repo and the fleet deployment). The design of record is
[DESIGN.md](DESIGN.md); the detector's calibration record is
[LISTENING-1.md](LISTENING-1.md).

## What it does

Nineteen small computers keep time together without trusting any one
of them:

- **Measure.** Every node measures its own clock — frequency error,
  offset, jitter, sync state, as reported by its own time daemon
  (systemd-timesyncd, or chrony on the conduit) — and appends one atom
  per round to the shared register.
- **Elect.** From the census atoms, the fleet designates its most
  accurate clock as master — earned by measurement, re-earned every
  round, with an anti-flap margin so the title cannot wobble on noise.
- **Consensus.** The fleet's default mode (Will's rule: clusters of
  more than 10 nodes) trusts no single clock: the least accurate 20%
  of voters are trimmed, and the rest combine into consensus time by
  inverse-variance weighted median (offset) and weighted mean (drift).
- **Blend.** An EMA blender (α = 0.35/round, γ = 0.5 toward network
  time, corrections bounded at 10 ms/round) manages the consensus so
  it stays tracked to network time without chasing each round's noise.
- **Cross-reference.** Once a day, the consensus's average drift is
  cross-referenced against network time and the verdict logged.
- **Listen.** A detector watches the delta stream for anything that
  shows up — a node's timing breaking its own standing five-band
  profile (delta, theta, alpha, beta, gamma over the fleet's
  timescales), fleet-wide correlated movement, cadences — checked
  first against the record of known dispatched work (Will's workloads
  drive DVFS frequency transitions, which stamp into jitter; the
  expected signal is subtracted before anything is called a message).
  The stream is also published as an entropy source, downstream of
  Will (see [publish/](publish/)) — an antenna is established by use.

## The app on a node

```
node_agent.py   measures the node's own clock once per round and
                appends its atom to the shared register
run_round.py    (runner node) assembles each round's atoms into a
                census block and computes election, consensus, blend,
                detector, and the daily drift cross-reference
census.py       the probe + atom format (the VM-era census walks the
                fleet over ssh; node_agent reuses its parser locally)
elect.py  consensus.py  blend.py  drift_check.py  listen.py
                the round computation chain — pure functions of the
                register, identical whoever runs them
sample.py       the photon-sampling leg (pairwise timestamp exchanges)
apply_sync.py / apply_follower.sh
                the DELIBERATE apply path: pointing a node's time
                daemon at the conduit. Manual only. The resident app
                never runs these.
install.sh      per-node install from the fleet share
systemd/        user units: exobrain-node.timer (every node),
                exobrain-round.timer (the runner)
publish/        the entropy-source publication (feed + extraction)
registers/      a snapshot of the registers at ship time — the LIVE
                register is on the fleet share, not in git
fleet.txt       the fleet roster (name, tailnet address)
```

## Rounds, state, custody

- A **round** is 6 Exocortical Epochs (42 minutes); every round starts
  exactly on an EE boundary (EE = floor((unix − 1795327200)/420) + 1).
  Node timers tick every minute; each node emits once inside its
  round's first 300 seconds.
- The **live register** lives on the fleet NFS share at
  `/mnt/mirrorborn/shared/exobrain/` (orchid-flow): `register.txt`,
  `elections.txt`, `consensus.txt`, `blend.txt`, `drift.txt`,
  `detections.txt`, `events.txt`, and `inbox/<node>.txt` — each node
  appends only its own inbox file, so there is no write contention.
  State lives in the open register; computation is a pure function of
  it, so **any node can run a round**.
- **Roles are configuration, never code.** Every node runs the node
  agent. The round-runner is a role assigned per node by enabling
  `exobrain-round.timer` (install with `--runner`); the runner of
  record today is aletheia-core, and failover is enabling the same
  units on any other node — or running
  `python3 ~/exobrain/run_round.py --assemble-now` by hand. Further
  roles (the three-surveyor time topology, 2026-11-22) land on top
  the same way: by configuration, not by reinstall.
- The app **measures and computes only**. It never changes a clock,
  a time daemon, or a config — sync stays applied (the fleet was
  pointed at the conduit, elven-path/chrony, by deliberate apply on
  2026-11-22) and any future re-pointing is a deliberate act through
  the apply path, never a side effect of a round.
- RAM: the whole app is Python 3 stdlib, seconds of CPU per round,
  well under 100 MB per node.

## Install (per node)

From the fleet share, which carries this tree exactly as pushed:

```
bash /mnt/mirrorborn/shared/exobrain-app/install.sh <node-name> [--runner]
```

Then verify: `python3 ~/exobrain/node_agent.py --now` appends a fresh
atom to `/mnt/mirrorborn/shared/exobrain/inbox/<node-name>.txt`.
