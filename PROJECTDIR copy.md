```
hive/
├── README.md
├── pyproject.toml
├── .env.example                    # credential env var names, no values
├── .github/workflows/tests.yml     # pytest + demo on every push
├── config/
│   └── hive.example.yaml           # Queen + consent settings, change windows
├── docs/
│   └── ARCHITECTURE.md             # the collective, from a network architect's view
│
├── queen/                          # the coordinator
│   ├── models.py                   # every record on the blackboard
│   ├── blackboard.py               # storage protocol + in-memory store
│   ├── audit.py                    # append-only decision log
│   ├── order.py                    # dedup, storms, correlation by failure domain
│   ├── classify.py                 # signal -> required skills (rules; LLM later)
│   ├── registry.py                 # drones, skill matching, severing
│   ├── dispatch.py                 # directives, routing, leases, retries
│   ├── drift.py                    # desired vs observed
│   ├── consent.py                  # identity, votes bound to content, PR approvals
│   ├── governance.py               # freeze, windows, domain locks, canary
│   ├── queen.py                    # leader lease, tick loop, result hooks
│   ├── link.py                     # failover client to whichever Queen leads
│   └── settings.py                 # YAML -> QueenConfig, ConsentPolicy
│
├── drones/                         # one module per unimatrix
│   ├── base.py                     # join, receive, lease renewal, report through failover
│   ├── checks.py                   # shared health checks (REP, PTP, interfaces)
│   ├── sense.py                    # collect.show, collect.config, backup.run, inventory
│   ├── mapper.py                   # topology.map: domains + port membership
│   ├── sentinel.py                 # patrols, baselines, HEALTH signals
│   ├── tshoot.py                   # tshoot, .rep, .ptp, .interface, .routing
│   ├── intent.py                   # intent.publish, drift.remediate, config.propose
│   ├── intent_repo.py              # LocalIntentRepo (PRs), GitHubIntentRepo skeleton
│   ├── guardian.py                 # verify.change: lint, mgmt plane, pre-conditions, rollback
│   ├── deployer.py                 # config.apply: pre-check, timer, soak, post-check
│   └── builder.py                  # template.build: TextFSM drafts
│
├── netskills/                      # the skill gateway
│   ├── gateway.py                  # PolicyGateway, BoundGateway, NetmikoGateway (read-only)
│   ├── normalize.py                # config <-> normalized dict, remediation, rollback
│   └── fake.py                     # simulated plant: REP ring, PTP, faults, syslog
│
├── intent/devices/*.yaml           # example intent (ring 1: ie-sw1..ie-sw4)
├── hive_lab.py                     # complete lab collective
├── scripts/demo.py                 # six-act demo
├── skills_out/                     # REP and PTP-over-REP skill knowledge
└── tests/                          # unit (fake clock) + end-to-end (lab)
```
