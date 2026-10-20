# hive
Resistance is futile

```
          Events (syslog, SNMP traps)     Humans (chat, approvals)
                     │                              │
              ┌──────▼──────────────────────────────▼─────┐
              │            Queen (coordinator)            │
              │  turns events into tasks, assigns owners, │
              │  enforces quorum and approval rules       │
              └──────┬──────────────────────────┬─────────┘
                     │ publish / claim          │ read / write
   ┌─────────────────▼───────────────┐   ┌─────▼──────────────────────┐
   │ Worker agents (skills)          │   │ Blackboard (shared memory)  │
   │  Scout      inventory, backup   │◄─►│  facts    + provenance, TTL │
   │  Parser     show → TextFSM      │   │  tasks    + leases           │
   │  Builder    writes new templates│   │  templates + review status   │
   │  Analyst    diagnoses faults    │   │  events   append-only log    │
   │  Guardian   verifies changes    │   │  runbooks vector index       │
   └─────────────────┬───────────────┘   └────────────────────────────┘
                     │ tool calls (allowlisted)
              ┌──────▼──────────────────┐
              │ Skill gateway           │  policy, allowlist, rate limits,
              │ (from phase 1)          │  per-device locks, audit log
              └──────┬──────────────────┘
                     │
                  Devices
```
