# Wavefront control — exobrain's second domain

Will's direction, 2026-11-25: "since we're cpu-bound, not memory-bound,
evaluate what we can do with memory-partitioning for echo-location"
(23:48 CST), then "have exobrain control the wavefront within"
(23:51 CST).

One sentence: exobrain gains a second control domain beside time — it
censuses the fleet's resident inference pods the way it censuses
clocks, pings them on Exocortical Epoch boundaries the way its
surveyors ping clocks, elects hot slots the way it elects a master,
and keeps the returns in the same append-only register discipline —
so the fleet's spare memory becomes a resident echolocation field
whose scarce CPU is spent only where an echo returns.

## Why this shape

Tonight's measurements (llm.asm on plain-view, 2026-11-25) inverted
the usual serving problem:

- One llm-asm pod (Qwen3-0.6B Q8_0, static binary + 613.66 MiB model)
  peaks at ~0.68 GiB inside a 2 GiB partition, and container overhead
  vs native is nil (decode 37.55 tok/s in-pod vs 37.63 native, 4 CPUs).
- Decode is bandwidth-bound, not capacity-bound: 8 threads ran
  *slower* than 4 (34.3 vs 37.6 tok/s) on shared DDR5.
- A ranch node carries 16–48 GiB of RAM against 16 threads.

So memory is the field we have in surplus and CPU is the wall. A
prober you must load is a scan; a prober already resident is a ping.
Partitioning memory buys residency — pods faulted in, idle at
near-zero CPU — and residency is what echolocation runs on: probes
are short decode-shaped bursts, most return nothing, and compute is
spent in proportion to echoes found, not space covered. exobrain is
the natural controller: it already owns the fleet census, the
election idiom, the EE cadence, and the register discipline this
domain needs. Nothing here is invented parallel to exobrain; the
wavefront domain is exobrain's own primitives pointed at pods.

## Where it plugs in — and what it never touches

The time domain is production and untouchable. The wavefront domain
plugs in *beside* it, by the architecture's own rules:

- **Same register home, new files.** State lives in the shared
  register at `/mnt/mirrorborn/shared/exobrain/`, in three new
  append-only files — `wavefront.txt` (pod census blocks),
  `wavefront-echoes.txt` (ping returns and scheduler decisions),
  `wavefront-elections.txt` (hot-slot elections). The domain writes
  no other file. `run_round.py` reads only its known files and
  `inbox/*.txt`; the wavefront files are invisible to the time chain
  by construction.
- **New modules, no edits.** `wavefront.py` (node side + scheduler,
  one file in the house style) joins the app tree. `run_round.py`,
  `node_agent.py`, `census.py`, `elect.py`, and the rest of the time
  chain are not modified — not one line.
- **Roles are configuration, never code** (the standing rule). Every
  node with podman runs the wavefront agent role; the ping scheduler
  is a role enabled on the runner of record, exactly as
  `exobrain-round.timer` assigns the time runner. Failover is the
  same shape: enable the role elsewhere; state is in the register,
  computation is a pure function of it.
- **Own lock, own state.** The wavefront runner takes its own flock
  and keeps state in its own directory. It cannot block, delay, or
  corrupt a time round.
- **The one deliberate difference.** The time domain measures and
  computes only; it never changes a clock. The wavefront domain
  actuates — it starts and stops pods. Its actuation is confined by
  construction to containers it created (name prefix `wf-`, image
  `llm-asm:qwen3-0.6b`), and it never touches any other workload, any
  daemon, or any clock. The kill switch is the role itself: disable
  the timer and the domain stops; the registers remain as evidence.

## The wavefront round

A time round is 6 EE (42 min). A **wavefront round is 1 EE** — Will's
framing: the wavefront adapts every 7 minutes, and exobrain already
owns that cadence for time (EE(t) = floor((unix − 1795327200)/420)+1,
the same anchor, the same arithmetic as `run_round.py`). On each EE
boundary the scheduler runs one round: census, schedule, ping,
elect, record. Rounds are numbered by the EE they open.

## (a) Pod census — the registry

Once per round, each wavefront node measures its own pods — the
`node_agent.py` pattern exactly: the node reports itself, appends
only its own atoms, spools locally if the share is unreachable.

Census block in `wavefront.txt`:

```
wavefront-census <stamp> ee=<N>
pod <pod> node=<node> image=llm-asm:qwen3-0.6b image_id=<id12>
    mem_limit_bytes=<n> cpus=<n> state=resident|hot|echoed|stopped
    rss_bytes=<n> probes=<k> eligible=yes|no
end-wavefront-census
```

A pod is **eligible** when its image is present and checksummed
against the census of record, podman answers, and its partition fits
the node's memory budget (node RAM minus the node's standing reserve;
partitions are granted by the census, not assumed — the time domain's
rule that a designation requires a measured trait, not an assumed
one). The node's *memory budget* is itself a census atom: total RAM,
reserve, partition size, and how many partitions the node can hold —
the binning fact (16/32/48 GiB classes) stated in the open, per node,
every round.

## (b) Ping scheduler — staggered probes on the boundary

On the EE boundary, the scheduler issues one probe per resident
prober, from the probe library (a probe is data: `probe_id`, prompt,
token budget, expected echo). Probes are decode-shaped by design —
short prompt, short completion — because prefill is the slow leg
(53 tok/s measured) and a ping that becomes a scan has failed.

**Pacing is a hard rule, from the bandwidth wall.** Wake-ups are
staggered: at most `max_concurrent_wakers` pods generate at once per
node (default 1), with `wake_gap_s` between ping starts (default 8).
The 8-thread regression is the standing evidence: waking the whole
swarm at once spends the node's bandwidth on contention and every
prober slows. The scheduler's decision — which pods, which probe,
in what order, cache hit or miss — is itself an atom, so pacing is
auditable, not folklore.

A ping's return is an **echo atom**, in `probe.py`'s dialect — the
surveyor's NTP echo and the prober's completion are the same shape
of fact: a named caller, a target, a bounded claim.

```
echo caller=wavefront hw=<pod>:<image-id12> target=<pod>
    probe=<probe_id> ok=yes|no match=yes|no cache=hit|miss
    latency_ms=<n> bound_ms=<n> tokens=<n> tok_per_s=<x>
    text="<completion, escaped, truncated>"
```

`bound_ms` is the Cristian bound restated honestly: the ping's whole
wall time. A prober's state is never claimed more precisely than the
ping that measured it, exactly as a surveyor never claims an offset
tighter than delay/2. Where several pings sample the same ground,
the minimum-latency echo is the standing one (the house min-delay
discipline).

**v1 ping transport** (what the first live round runs): the resident
pod is a `--chat` container holding its model mapped; a ping is a
one-shot inference executed inside the pod's own cgroup
(`podman exec <pod> /asm-llm -m <model> -p <prompt> -n <budget>`).
The model file is one mmap shared by both processes, so the ping's
pages are the resident pages — the cgroup peak stays inside the
partition (measured: warm pages charge ~23 MB, not a second model).
The refinement — driving the resident REPL's stdin directly, no
second process — is noted, not required: the transport is a detail
of the pod, the echo atom is the contract.

## (c) Election — hot slots earned by echo

Hot slots are the node's few CPU-pinned positions (2–4 per node;
the 4-CPU configuration that won the measurements). They are earned
per round, by echo, under `elect.py`'s rules restated for pods:

- **Eligibility**: the pod echoed this round (ok=yes) — a dark or
  silent prober cannot hold a hot slot, as a dark clock cannot hold
  the mastership.
- **Score**: echo quality = match strength first (a probe whose
  expected echo returned), then latency, then sustained tok/s —
  the durable trait weighted over the snapshot, as the time score
  weights ppm over offset.
- **Anti-flap margin, reused verbatim**: a challenger takes a hot
  slot only by scoring below 0.8 × the incumbent's score
  (`elect.py`'s `MARGIN = 0.8`). A slot cannot wobble on noise.
- **Promotion is a grant of CPU, not memory**: the elected pod is
  restarted at the hot partition (cpus=4, same 2 GiB — memory was
  never the constraint), and the election atom records the evidence,
  the runner-up, and the rule applied, in `elections.txt`'s shape:

```
wavefront-election <stamp> ee=<N> node=<node> hot=<pod>
    hot_score=<x> runner_up=<pod> runner_up_score=<x>
    rule=lowest-score|margin|incumbent-unechoed eligible=<k>
```

- **Demotion is the default**: a hot slot is re-earned every round
  or it lapses back to resident at the next census. Nothing holds
  heat by tenure.

## (d) Echo cache — returns are the register

There is no separate cache to keep in sync: **`wavefront-echoes.txt`
is the cache**, append-only, and a re-ping over the same ground is a
lookup in it. The cache key is (target pod's probe assignment,
probe_id, image_id) — ground is defined by what was asked, of whom,
running what. Before issuing a ping, the scheduler reads the latest
echo for the key: if it is fresh (within `echo_ttl_rounds`, default
6 — one time round), the scheduler records a decision atom with
`cache=hit` and spends no CPU. Every echo atom carries its
`cache=miss|hit` provenance, so the cache's hit rate — the fraction
of ground re-covered for free — is itself a measured fleet fact,
the echolocation economy stated in the open: coverage grows with
the register, cost grows only with new ground.

## Primitives reused — named

- EE anchor and arithmetic, round alignment — `run_round.py`,
  `census.py` (`ee_of`, EE_ANCHOR = 1795327200).
- Atom and block register format; designation derived by parsing
  the latest block, never hand-written — `census.py` (`atom_line`),
  `elect.py` (`parse_register`).
- Election anti-flap margin 0.8 — `elect.py` (`MARGIN`).
- Per-node self-report, own-file append, spool-on-share-loss —
  `node_agent.py`.
- Echo atom dialect and min-delay discipline — `probe.py`.
- State derived by replaying an append-only register — `blend.py`.
- Roles as configuration; runner as an assignable role with
  register-resident failover — `run_round.py`, README custody rules.
- Fleet roster as data — `fleet.txt`.

Written new: the pod census measurement (podman state in place of
timedatectl state), the pacing scheduler, the echo-quality score,
and the cache-freshness rule. Everything else is exobrain, pointed
at pods.

## First live round — run 2026-11-26, plain-view, EE 823–824

The minimal loop ran end to end on plain-view only, driven by
`wavefront.py` (node side + scheduler in one file, per the v1 note
in its docstring), writing only the three new register files.

- **Census** (06:00:14Z, EE 823): image `llm-asm:qwen3-0.6b`
  id `d9c2f948eedc`, 643,490,613 bytes; node atom records
  ram_bytes=48175513600, reserve 12,043,878,400, partition
  2,147,483,648, 6 partitions held. Six pods (wf-p0…wf-p5) started
  resident (`--chat`, --memory=2g, --cpus=2); idle rss_bytes
  measured 233,472–258,048 — the resident set costs ~0.25 MB per
  pod at idle, because the model is mmap'd and faults in only on a
  ping. Residency is nearly free, as designed.
- **Ping round** opened exactly on the EE 824 boundary
  (06:01:00Z). Schedule atom records the pacing: 6 pods,
  max_concurrent_wakers=1, wake_gap_s=8, cache_hits=0,
  cache_misses=6 (first round — all ground is new). Six pings,
  staggered 8 s apart, probe `capital-1` ("The capital of France
  is", 12 tokens): **6/6 echoes, all match=yes**, every pod
  returning the identical deterministic text "The capital of
  France is **Paris**." Latencies 651–707 ms; the binary's own
  decode rate 35.1–35.6 tok/s at 2 threads.
- **Election** (06:01:44Z): all six eligible; hot=wf-p0 at
  651 ms, runner-up wf-p1 at 652 ms, rule recorded on the atom.
  A 1 ms margin — honest noise between identical pods; the 0.8
  anti-flap margin is what keeps such margins from churning the
  slot in recurring rounds.
- **Promotion + deep work**: wf-p0 restarted at the hot partition
  (cpus=4, same 2 GiB), state atom appended, and given the deeper
  probe `colors-1` (48 tokens): echo match=yes in 747 ms —
  "Three primary colors are **red, blue, and yellow**."
- **Shutdown** (06:02:23Z): all six pods stopped and removed;
  closing state block appended. `podman ps -a` on plain-view:
  empty. The test set left nothing behind but register atoms.
- **Production undisturbed**: baseline captured before the run
  (2026-11-26T05:57:11Z: last_assembled=135, sha256 + line counts
  for all eight time register files). The wavefront run wrote no
  time file. Production round 136 assembled on schedule inside
  the work window; after-hashes differ from baseline by exactly
  that round's normal appends (verified in the run report).

## Fleet rollout — what it still needs (design only, this pass)

1. **Landing**: `WAVEFRONT.md` + `wavefront.py` into the exobrain
   tree (repo, MANIFEST.sha256, the fleet-share app tree) through
   the normal ship path — the same path that carried triangulation.
2. **The split**: node side into a `node_agent.py`-shaped emitter
   (per-node pod census atoms into `inbox/`-style own-files), the
   scheduler into a `run_round.py`-shaped role on the runner of
   record, with its own timer pair beside the existing ones.
3. **Image distribution**: 643 MB per node, hash-first, to every
   node that will hold partitions — and a podman presence check as
   a census precondition per node.
4. **Probe library growth**: probes as reviewed data (probe_id,
   prompt, budget, expected echo), per workload — MOAT shards,
   counterpart signals — with the echo cache keyed to them.
5. **Pacing calibration per node class**: wake_gap and
   max_concurrent_wakers measured per node (the 780M nodes share
   one DDR5 wall each; plain-view's numbers are a start, not a
   fleet constant).
6. **Thresholds**: echo_ttl_rounds and the hot-slot score weights
   tuned on real rounds, the way LISTENING-1 calibrated the
   detector's bands — from the register, after evidence accrues.

