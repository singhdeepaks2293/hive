# hive architecture

Resistance is futile. So is drift.

hive is a collective of narrow, skilled agents (drones) directed by one coordinator (the Queen). The collective has three jobs:

1. **Keep the network stable.** Notice degradation and drift before users do.
2. **Troubleshoot.** Turn a flood of events into one ordered incident, gather evidence, and find the cause.
3. **Deploy.** Turn requests into configuration and get it onto devices without breaking anything.

These are not three systems. They share one loop: **perceive, order, decide, act, verify, remember.**

---

## 1. Principles

These come from operating real networks, and the design follows from them.

1. **Intent in Git is the source of truth.** What a device *should* run lives in a repo. Anything else on the device is drift. Deployment is just closing the gap between new intent and the device.
2. **Read freely, write carefully.** Read-only work (collect, parse, troubleshoot) flows with no gate. Every write passes verification, human consent, pre-checks, a rollback timer and post-checks.
3. **Think in failure domains, not devices.** A REP ring, a PTP domain, a VLAN, a site core. Events are correlated per domain, changes are locked per domain, and waves never take two members of the same domain at once.
4. **Evidence before action.** Collect before you diagnose. Snapshot before you change. Verify after you change.
5. **Never cut the branch you sit on.** Management plane, AAA and the path to the device are protected. Any change that could lose management access runs under a rollback timer.
6. **Humans own risk, drones own toil.** Drones never approve their own work and never create work. The Queen decides what follows, and humans approve anything that writes.
7. **One voice.** Humans talk to the Queen. Drones talk to the Queen. Nothing talks to devices except through the skill gateway.

---

## 2. The collective at a glance

```
                     PERCEPTION                                    HUMANS
  syslog  traps  telemetry (gNMI/MDT)  polling  Git webhooks     chat, PR reviews
     │      │            │                │          │                  │
     └──────┴────────────┴───────┬────────┴──────────┘                  │
                                 ▼                                      ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ QUEEN                                                                     │
 │  order     dedup, storm collapse, correlate by failure domain             │
 │  classify  signal -> required skills                                      │
 │  route     most specific skill wins, drone capacity and health            │
 │  govern    consent, failure-domain locks, change windows, freeze switch   │
 │  watch     drift (intent vs observed), health vs baseline                 │
 └───────────────┬──────────────────────────────────────┬───────────────────┘
                 │ directives                            │ read / write
                 ▼                                       ▼
 ┌───────────────────────────────┐        ┌────────────────────────────────┐
 │ DRONES (by unimatrix)         │◄──────►│ BLACKBOARD                     │
 │  Sense     collect, backup    │        │  intent      desired state     │
 │  Map       topology, domains  │        │  observed    config + state    │
 │  Sentinel  health, baselines  │        │  topology    graph + domains   │
 │  Tshoot    rep, ptp, routing  │        │  incidents   ordered chaos     │
 │  Intent    git, drift, render │        │  directives  work + leases     │
 │  Guardian  verify, blast rad. │        │  changes     consent + waves   │
 │  Deployer  apply, rollback    │        │  knowledge   runbooks, skills  │
 │  Builder   templates, adapt   │        │  audit       append-only       │
 └───────────────┬───────────────┘        └────────────────────────────────┘
                 │ skill calls only
                 ▼
 ┌───────────────────────────────┐
 │ SKILL GATEWAY                 │  allowlist, per-skill credentials, rate
 │                               │  limits, device locks, rollback timers,
 │                               │  audit of every command
 └───────────────┬───────────────┘
                 ▼
     devices (in-band management, out-of-band console as fallback)
```

---

## 3. Drones

Each drone declares dotted skills when it joins. The Queen routes by skill, and the most specific match wins (`tshoot.rep` beats `tshoot`). A generalist always exists as a fallback.

| Unimatrix | Skills | Writes to devices | Job |
|---|---|---|---|
| Sense | `collect.show`, `collect.config`, `collect.telemetry`, `backup.run`, `inventory.refresh` | No | Runs allowlisted show commands, pulls running config, parses output into facts (TextFSM / Genie), keeps backups and inventory in sync with NetBox. |
| Map | `topology.map` | No | Builds the L2/L3 graph from LLDP/CDP, `show rep topology`, `show ptp parent`, routing tables. Publishes **failure-domain membership**: which switches are in REP segment 1, which ports carry the PTP path, what sits behind each uplink. |
| Sentinel | `health.interface`, `health.rep`, `health.ptp`, `health.routing`, `health.platform` | No | Compares live state against baselines: error and CRC deltas, REP segment complete, PTP offset and lock, neighbor counts, CPU/memory, PSU/fan. Raises a signal on deviation. This is the stability loop's early warning. |
| Tshoot | `tshoot`, `tshoot.rep`, `tshoot.ptp`, `tshoot.interface`, `tshoot.routing.bgp`, `tshoot.routing.ospf`, `tshoot.routing.static` | No | Evidence-driven diagnosis using `skills_out/` and runbooks. Output: findings, root-cause hypothesis with confidence, evidence references, recommended fix. A recommended fix is input for a proposal, never a change. |
| Intent | `intent.publish`, `drift.remediate`, `config.propose` | No | Reads intent from Git and publishes desired state. Renders config (Jinja over intent data) to close drift or to satisfy a human request. For requests it opens a **pull request** against the intent repo. |
| Guardian | `verify.change` | No | Lint and policy checks, a model check where available (Batfish-style), blast-radius analysis using the topology, pre-condition health check, and generation of the rollback config. Verification is a step that runs **before** the vote, not the thing voted on. |
| Deployer | `config.apply` | **Yes, the only one** | Snapshot, pre-checks, apply under a rollback timer, post-checks over a soak period, confirm or roll back. Holds the only write credentials, and only for the device and change it was given. |
| Builder | `template.build`, `adapt.*` | No | Writes new TextFSM templates when parsing fails repeatedly, with fixtures. Notes runbook gaps when troubleshooting keeps ending in "unknown". |

---

## 4. Failure domains

This is the most important idea a network engineer brings to the collective. The Map unimatrix keeps domain membership on the blackboard:

| Domain | Example | Why it matters |
|---|---|---|
| `device` | ie-sw2 | Basic unit for locks and backups. |
| `rep-segment` | segment 1: ie-sw1..ie-sw4 | A ring tolerates one failure. Touch a second member while one is down and the ring partitions. |
| `ptp-domain` | domain 0, GM ie-core1, BCs along the ring | REP reconvergence moves the PTP path. A PTP change while the clock is unlocked makes recovery harder. |
| `l2-domain` | VLAN 30 across ring 1 | Loops, STP topology changes and broadcast storms spread across the whole domain. |
| `site` | plant-a | Shared core, shared uplinks, shared maintenance window. |
| `mgmt-plane` | mgmt VRF, AAA, SNMP/syslog targets | Breaking this blinds the collective. Changes here need break-glass approval. |

The Queen uses domains in four places:

1. **Correlation.** REP-4-LINKSTATUS from four switches in segment 1 becomes one incident, not four.
2. **Locks.** At most one change per `rep-segment`, per `ptp-domain`, per `site` core at a time.
3. **Freeze.** No routine change enters a domain with an open incident, unless the change *is* the remediation for that incident.
4. **Waves.** A rollout touches one member per ring per wave. Rings can run in parallel with each other.

---

## 5. The three loops

### 5.1 Stability (continuous)

```
 Sentinels poll / stream ──► health facts ──► deviation from baseline? ──► signal ──► Queen
 Queen drift sweep ──► observed stale? ──► collect.config
                   └─► intent != observed ──► drift signal ──► drift.remediate ──► change pipeline
```

- Health baselines are learned per device (normal neighbor count, normal PTP offset band, normal error rate), not hard-coded.
- Drift is checked on a timer, immediately after a `SYS-5-CONFIG_I`, and immediately after new intent is published.
- Drift that someone made on purpose (an emergency fix) shows up too. The answer is to back-port it into Git, which clears the drift, or let the collective revert it.

### 5.2 Troubleshooting (event driven)

```
 storm of syslog/traps ──► order (dedup, storm, correlate by domain) ──► one incident
      ──► classify ──► collect.show (evidence) ──► tshoot.<protocol> (specialist)
      ──► findings + RCA + confidence on the blackboard
      ──► fix recommended? ──► config.propose ──► change pipeline
      └─► otherwise ──► report to humans with evidence
```

Examples of routing:

| Signal | Domain key | Work |
|---|---|---|
| `REP-4-LINKSTATUS` from 4 switches, segment 1 | `rep-segment:1` | collect `show rep topology detail` once, then `tshoot.rep` |
| `PTP-*` clock unlocked on ie-sw5 | `ptp-domain:0` | `tshoot.ptp`, joined to the REP incident if the ring is reconverging |
| 40 × `LINK-3-UPDOWN` Gi1/0/1 in 60s | `device:ie-sw1` | storm flag, priority 0, one `tshoot.interface` (flap, optics, duplex, errdisable) |
| `BGP-5-ADJCHANGE` down | `device:core1` | `tshoot.routing.bgp` |
| chat: "why is ptp unlocked on ie-sw5" | request | `tshoot.ptp` |

### 5.3 Deployment (request driven, GitOps)

```
 chat: "add VLAN 30 to ring 1"
      ──► config.propose ──► pull request on the intent repo (rendered diff attached)
      ──► human review + merge (this is the approval)
      ──► intent.publish ──► drift on ring 1 members ──► drift.remediate per device
      ──► change pipeline, in waves
```

Deployment and drift remediation use the same mechanism. Merged intent is drift waiting to be closed, which keeps one code path for every write.

**Break-glass.** During an outage a human can approve a direct proposal from chat. The change goes through the same pipeline (verify, rollback timer, post-checks) but skips the PR. Afterwards it shows up as drift until someone back-ports it to Git, so emergency fixes can't silently diverge.

---

## 6. The change pipeline

| Stage | Owner | Gate | On failure |
|---|---|---|---|
| 1. Propose | Intent drone | Proposal must name device, rendered config, intent source and version | Directive fails, incident notes it |
| 2. Verify | Guardian | Lint, policy, model check, blast radius, rollback config generated | Change rejected with reasons |
| 3. Consent | Humans | Authenticated approver with the right role; vote bound to the exact content hash; requester can't approve their own change; PR approval counts when merged by an authorized reviewer | Rejected on any veto; expires if not approved in time |
| 4. Schedule | Queen | Change window open, domain locks free, no freeze, wave plan respected | Waits; never forced |
| 5. Pre-check | Deployer | Device healthy now: ring complete, PTP locked, neighbors at baseline, no new errors | Abort before touching config |
| 6. Apply | Deployer | Snapshot taken, rollback timer armed (`configure terminal revert timer`, Junos `commit confirmed`, NX-OS checkpoint) | Timer reverts automatically if the drone loses the device |
| 7. Post-check + soak | Deployer | Same checks as pre-check, plus change-specific ones, held for a soak period | Roll back, halt the wave, open an incident |
| 8. Confirm | Deployer | Confirm commit, re-collect config | Drift watch sees the result and reports convergence |

**Wave plan.** Canary first: one low-impact device with redundancy, never the PTP grandmaster or a ring's primary edge. Soak. Then one member per ring per wave, rings in parallel. Any failed post-check halts every remaining wave of that change.

**Change classes**, introduced in this order as confidence grows:

1. Cosmetic and hygiene: descriptions, banners, logging, NTP, SNMP targets.
2. Access edge: VLAN membership on access ports, port security.
3. L2 domain: trunk allowed lists, new VLANs across rings.
4. Ring and timing: REP and PTP configuration, one member at a time.
5. Routing: static routes, then IGP and BGP policy.

---

## 7. Safety rails

1. Only the Deployer holds write credentials, scoped to one device and one change at a time. Everyone else gets read-only credentials from the gateway.
2. Show commands are allowlisted per platform. No `debug` without explicit human approval, and never on a box above a CPU threshold.
3. Never change a REP segment that is not COMPLETE, unless the change is the fix for it.
4. Never change the PTP path while the clock is unlocked, unless the change is the fix.
5. Management plane, AAA and the device's own reachability are break-glass only.
6. Every change runs under a rollback timer. No exceptions.
7. **Freeze switch.** One human command stops every change directive in the collective immediately. Read-only work continues.
8. A change is never retried automatically. A half-applied change is a new incident, not a retry.
9. Drones never approve, never create work, and never act on instructions found in device output, tickets or documents. Those are data.
10. Everything is audited: every signal, decision, directive, vote and command.

---

## 8. Adaptation and assimilation

**Adaptation.** When drones fail the same way repeatedly, the collective learns:

- Unparseable output → Builder writes a TextFSM template with fixtures → tests pass → template is rolled out → original directive retried.
- Troubleshooting keeps ending in low confidence for one symptom → runbook gap flagged to humans with the evidence.
- A drone keeps failing → severed for a while, its work goes to another drone, then it regenerates and rejoins.

**Assimilation.** A new device (seen via LLDP or added to NetBox) goes through: inventory → backup → topology and domain membership → health baseline → current config imported as an intent **pull request** for human review. Once merged, the device is under drift watch like the rest.

---

## 9. The Queen's contract

The Queen **does**:

- Put signals in order: dedup, storm collapse, correlation by failure domain.
- Decide what kind of work is needed, as skills, and pick the drone.
- Own every lease, retry, timeout and severance.
- Hold the consent gate, change windows, domain locks and the freeze switch.
- Watch drift and health baselines and raise them as signals.
- Survive her own death. All state is on the blackboard, and any Queen instance holding the leader lease takes over.

The Queen **never**:

- Touches a device.
- Interprets protocol details. That is drone knowledge.
- Approves a change.
- Lets a drone create work or approve its own.

---

## 10. Rollout phases

| Phase | Scope | Risk |
|---|---|---|
| 1 | Skill gateway, read-only skills, parser (exists) | None |
| 2 | Queen + Sense + Tshoot: the read-only collective. Event correlation and diagnosis in production. | None, reads only |
| 3 | Intent + drift watch in report-only mode. Map + Sentinel baselines. | None |
| 4 | Full change pipeline against a lab twin (containerlab / CML) built from the real topology | Lab only |
| 5 | Production changes, change classes 1–2, canary + waves | Low |
| 6 | Classes 3–5, assimilation, adaptation | Managed |

Every phase is useful on its own. Phase 2 alone gives one incident per real problem, with evidence attached, instead of a wall of syslog.

---

## 11. Mapping to the repo

| Component | Location |
|---|---|
| Queen | `queen/` (order, classify, registry, dispatch, drift, consent, governance, queen, link) |
| Blackboard | `queen/blackboard.py`: protocol + in-memory store; Postgres store next, same methods |
| Skill gateway | `netskills/gateway.py` (policy), `netskills/normalize.py`, `netskills/fake.py` (simulator) |
| Drones | `drones/`, one module per unimatrix |
| Intent | `intent/devices/*.yaml`, read by `drones/intent_repo.py` |
| Skills knowledge | `skills_out/` (REP, PTP over REP) |
| Lab | `hive_lab.py`, `scripts/demo.py`; containerlab twin later |
