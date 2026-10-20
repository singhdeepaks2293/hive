```
hive-netops/
├── README.md
├── pyproject.toml
├── .env.example                    # NET_USER, NET_PASS, NETBOX_URL (no real values)
├── config/
│   ├── nornir_config.yaml          # runner settings, credential env var names
│   └── allowlist/
│       ├── cisco_iosxe.yaml        # permitted show commands per platform
│       └── juniper_junos.yaml
│
├── ssh_connect/                    # your existing folder, kept as is
│   └── connect.py                  # open_session(host)
│
├── netskills/                      # shared gateway, used by every agent
│   ├── __init__.py
│   ├── gateway.py                  # single entry point for all skill calls
│   ├── policy.py                   # read-only enforcement, allowlist check
│   ├── schemas.py                  # pydantic models for inputs and outputs
│   ├── audit.py                    # audit records to Postgres
│   ├── inventory.py                # NetBox client
│   ├── device_io.py                # Nornir / netmiko runner
│   └── locks.py                    # per-device leases
│
├── blackboard/                     # shared memory for the hive
│   ├── __init__.py
│   ├── facts.py                    # Fact model, TTL, freshness
│   ├── tasks.py                    # task state and leases
│   ├── events.py                   # append-only event log
│   ├── store.py                    # Postgres implementation
│   └── migrations/
│       └── 001_init.sql
│
├── queen/
│   ├── __init__.py
│   ├── coordinator.py              # turns events and requests into tasks
│   └── router.py                   # picks the expert by protocol / task type
│
├── workers/                        # generic worker agents
│   ├── __init__.py
│   ├── scout.py                    # refreshes inventory and backups
│   ├── parser.py                   # runs show commands, returns parsed rows
│   ├── builder.py                  # generates TextFSM templates
│   ├── analyst.py                  # correlates facts and events
│   └── guardian.py                 # verifies proposed changes (phase 2)
│
├── helper/
│   ├── __init__.py
│   └── helper.py                   # independent verification on request
│
├── experts/
│   ├── __init__.py
│   ├── base.py                     # shared expert loop and report format
│   └── static/                     # first expert
│       ├── SKILL.md                # instructions, rules, when to use
│       ├── expert.py               # the seven-step checklist as code
│       ├── skills.py               # get_static_config, get_route_entry, ...
│       ├── report.py               # builds the JSON report for the Queen
│       └── prompts/
│           └── report_system.txt   # LLM instruction for writing the explanation
│
├── skills/                         # packaged skills (SKILL.md + scripts)
│   └── parse_output/
│       ├── SKILL.md
│       ├── scripts/
│       │   └── skill.py            # get_parsed_output (from earlier)
│       ├── templates/
│       │   └── custom/
│       │       ├── index           # Template,Platform,Command
│       │       └── *.textfsm
│       └── tests/fixtures/
│
├── wiki/                           # retrieval layer for troubleshooting docs
│   ├── __init__.py
│   ├── ingest.py                   # chunk and embed documents
│   ├── search.py                   # vector query, returns doc IDs and snippets
│   └── docs/
│       ├── own_runbooks/           # your own notes (safe to store)
│       ├── rfcs/                   # public RFCs (check license per source)
│       └── vendor/                 # vendor docs you are allowed to store
│
├── tests/
│   ├── unit/
│   │   ├── test_policy.py          # allowlist and blocklist
│   │   ├── test_facts.py           # TTL and freshness
│   │   ├── test_leases.py          # one session per device
│   │   └── test_static_checklist.py
│   ├── parser/                     # recorded show output → expected JSON
│   ├── lab/                        # containerlab topology and lab tests
│   │   ├── topology.clab.yml
│   │   └── test_static_cases.py    # the five static route cases
│   └── fixtures/
│       └── static/                 # recorded outputs for each static case
│
└── scripts/
    ├── run_lab.sh
    └── run_static_expert.py        # manual entry point for testing
```
