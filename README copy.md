# hive

Resistance is futile.

A Borg-style collective of agents for network operations. Narrow, skilled **drones** do the work; one **Queen** puts chaos into order. The collective keeps the network stable, troubleshoots faults, and deploys changes from intent in Git, with a human approving every write.

```
          Events (syslog, SNMP traps)     Humans (chat, PR reviews)
                     │                              │
              ┌──────▼──────────────────────────────▼─────┐
              │            Queen (coordinator)            │
              │  order: dedup, storms, failure domains    │
              │  route: most specific skill wins          │
              │  govern: consent, locks, freeze, canary   │
              │  watch: drift vs intent                   │
              └──────┬──────────────────────────┬─────────┘
                     │ directives               │ read / write
   ┌─────────────────▼───────────────┐   ┌──────▼───────────────────────┐
   │ Drones (by unimatrix)           │   │ Blackboard (shared memory)   │
   │  Sense     collect, backup      │◄─►│  intent / observed state     │
   │  Map       failure domains      │   │  incidents, directives       │
   │  Sentinel  health vs baseline   │   │  changes, votes, audit       │
   │  Tshoot    rep, ptp, interface  │   │                              │
   │  Intent    git, drift, render   │   │                              │
   │  Guardian  verify before vote   │   │                              │
   │  Deployer  the only writer      │   │                              │
   │  Builder   templates            │   │                              │
   └─────────────────┬───────────────┘   └──────────────────────────────┘
                     │ skill calls only
              ┌──────▼──────────────────┐
              │ Skill gateway           │  allowlist, write control,
              │                         │  rollback timers, audit
              └──────┬──────────────────┘
                     │
                  Devices
```

The full design, written from a network architect's point of view, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

```bash
pip install -e .[dev]
pytest -q                      # 38 tests, a few seconds
python scripts/demo.py         # the collective at work on a simulated REP ring
```

The demo runs a four-switch Cisco IE ring with PTP, simulated in `netskills/fake.py`:

1. **Assimilation.** Map drones publish failure domains. Sense drones observe config. The Queen finds that ie-sw2 has drifted from intent, the Intent drone renders the fix, and the Guardian verifies it.
2. **Chaos.** A ring link fails and an access port flaps: 25 signals.
3. **Order.** Three incidents: the REP ring (link-downs on ring ports included), the PTP domain, and a storm on the flapping port. Each goes to the right specialist, and each finding comes with advice.
4. **Consent.** A viewer and a forged token are refused. An approver says yes, but the change waits because its ring has an open incident. When the ring heals, the change runs.
5. **GitOps.** "Add camera VLAN 30 on ring 1" becomes a pull request. The author can't approve his own PR. After merge, the rollout runs canary-first, one ring member at a time, through a freeze and a Queen crash.
6. **Report.** Every write came from the Deployer, and the gateway refused the drones that tried otherwise.

## Layout

```
queen/          the Queen: signals, order, classify, registry, dispatch, drift,
                consent, governance, queen, link (failover client), blackboard
drones/         one module per unimatrix, plus base.py and checks.py
netskills/      skill gateway (policy), config normalization, Netmiko backend, simulator
intent/         example intent repo: one YAML file per device
config/         hive.example.yaml (Queen + consent settings)
hive_lab.py     wires a complete collective against the simulator
scripts/demo.py end-to-end demo
tests/          unit tests (fake clock) and end-to-end tests (lab)
docs/           ARCHITECTURE.md
skills_out/     protocol knowledge (REP, PTP over REP) the tshoot drones follow
```

## Where it stands

| Rollout phase | Status |
|---|---|
| 2. Read-only collective (Queen, Sense, Map, Sentinel, Tshoot) | Built and tested against the simulator. `NetmikoGateway` is read-only and ready for a lab. |
| 3. Intent + drift | Built. `LocalIntentRepo` works; `GitHubIntentRepo` is a documented skeleton. |
| 4. Change pipeline | Built against the simulator: guardian, consent, governance, deployer with rollback. |
| 5. Production writes | Not yet. `NetmikoGateway.apply` raises until platform rollback timers are wired. |

Known limits of this cut: the blackboard is in memory (Postgres store next), drones run in-process (a network transport next), free-text change requests need an NLU front-end to build the intent patch, and multi-valued settings (several NTP servers) need one key per value.

## License

MIT
