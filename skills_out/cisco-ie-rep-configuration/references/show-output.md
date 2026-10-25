# REP show command reference

## Commands

Per-interface REP status:
```
show interface [interface-id] rep [detail]
show interfaces rep detail
show interfaces GigabitEthernet1/4 rep detail
```

Topology:
```
show rep topology
show rep topology segment <segment-id>
```

Source syntax: `show rep topology [segment segment-id] [archive] [detail]`. Source gives examples only for `show rep topology` and `show rep topology segment 100`. NOT IN SOURCE: behavior of `archive` and `detail` keywords on device.

Fast beacon counters:
```
show platform rep beacon interface <interface-id>
```

ZTP PnP VLAN state:
```
show platform hardware l2 stp asic-num 0 vlan-id <pnp-vlan>
```

Recommended operational checks (source):
```
show processes cpu sorted
show platform resource
show rep topology
show logging
show tech-support
```

## show interfaces rep detail: field meanings

- `REP enabled` or disabled on the interface.
- `Segment-id: N (Primary Edge)`, `(Secondary Edge)`, or `(Segment)` for a regular port.
- `PortID`: unique port ID, port number combined with MAC address. Used by `rep block port id`.
- `Preferred flag`: Yes if set with `preferred`.
- `Operational Link Status`: `TWO_WAY` (neighbor detected and healthy), `NO_NEIGHBOR` (no REP peer found).
- `Current Key`: internal key used in neighbor negotiation.
- `Port Role`: see port roles below.
- `Blocked VLAN`: VLANs blocked on the port. `1-4094` in NO_NEIGHBOR. `<empty>` when nothing is blocked.
- `Admin-vlan`: REP admin VLAN in use.
- `REP-ZTP Status`: Enabled or Disabled at interface.
- `REP-ZTP PnP Status`: Unknown or In-Progress.
- `REP-ZTP PnP Vlan`: PnP startup VLAN number.
- `REP-ZTP Port Status`: Blocked or Unblocked for the PnP startup VLAN.
- `REP Segment Id Auto Discovery Status`: Enabled or Disabled (global).
- `REP Segment Id Type`: Auto or Manual.
- `Preempt Delay Timer`: disabled, or the configured seconds (for example `100 sec`).
- `LSL Ageout Timer`: milliseconds (for example `5000 ms`).
- `LSL Ageout Retries`: retry count.
- `Configured Load-balancing Block Port`: none, or the configured port ID.
- `Configured Load-balancing Block VLAN`: none, or the configured VLANs.
- `STCN Propagate to`: none, or segment or interface.
- Counters: `LSL PDU rx/tx`, `HFL PDU rx/tx`, `BPA TLV rx/tx`, `BPA (STCN, LSL)`, `BPA (STCN, HFL)`, `EPA-ELECTION TLV`, `EPA-COMMAND TLV`, `EPA-INFO TLV`. Source does not define each counter. NOT IN SOURCE: interpretation of counters beyond these labels.

## Port roles

- `Open`: forwarding. Normal state for non-blocked ports.
- `Alt`: alternate port, blocked for VLANs. One per segment when all ports are operational.
- `Fail`: a failed port (source: all ports move to the Failed state when a failure occurs).
- `Fail Logical Open`: forwarding for data path when more than two segment ports fail with no external neighbors configured. Source describes it under REP configuration guidelines.
- `Fail No Ext Neighbor`: port failed because no external neighbor is configured. Paired with `Fail Logical Open` in the guideline case.

## Example: primary edge uplink, healthy

```
Device# show interfaces GigabitEthernet1/4 rep detail
GigabitEthernet1/4 REP enabled
Segment-id: 3 (Primary Edge)
PortID: 03010015FA66FF80
Preferred flag: No
Operational Link Status: TWO_WAY
Current Key: 02040015FA66FF804050
Port Role: Open
Blocked VLAN:
Admin-vlan: 1
REP-ZTP Status: Disabled
Preempt Delay Timer: disabled
Configured Load-balancing Block Port: none
Configured Load-balancing Block VLAN: none
STCN Propagate to: none
LSL PDU rx: 999, tx: 652
HFL PDU rx: 0, tx: 0
BPA TLV rx: 500, tx: 4
```

## Example: downstream port in NO_NEIGHBOR with preempt delay and load balancing set

```
Device#show interface GigabitEthernet1/5 rep detail
GigabitEthernet1/5   REP enabled
Segment-id: 1 (Segment)
PortID: 019B380E4D9ACAC0
Preferred flag: No
Operational Link Status: NO_NEIGHBOR
Current Key: 019B380E4D9ACAC0696B
Port Role: Fail No Ext Neighbor
Blocked VLAN: 1-4094
Admin-vlan: 1
REP-ZTP Status: Disabled
Preempt Delay Timer: 100 sec
LSL Ageout Timer: 2000 ms
LSL Ageout Retries: 5
Configured Load-balancing Block Port: 09E9380E4D9ACAC0
Configured Load-balancing Block VLAN: 1-100
STCN Propagate to: segment 25
```

## Example: topology, all ports operational (one Alt per segment)

```
Device# show rep topology
REP Segment 1
BridgeName       PortName     Edge Role
---------------- ----------   ---- ----
10.64.106.63     Gi1/4        Pri  Open
10.64.106.228    Gi1/4             Open
10.64.106.228    Gi1/3             Open
10.64.106.67     Gi1/3             Open
10.64.106.67     Gi1/4             Alt
10.64.106.63     Gi1/4        Sec  Open
```

## Example: topology, segment failure

```
SWITCHA#sh rep topology
REP Segment 1
Warning: REP detects a segment failure, topology may be incomplete
BridgeName                       PortName   Edge Role
-------------------------------- ---------- ---- ----
SWITCHA                          Gi1/2      Sec  Open
SWITCHB                          Gi1/25          Open
SWITCHB                          Gi1/26          Fail
```

Interpretation: the failed port shows Role `Fail`. Source: each node builds this view from EPA packets, so nodes on different sides of a failure can show different partial topologies.

## Example: topology, closed ring healthy

```
SWITCHA#sh rep topology
REP Segment 1
BridgeName                       PortName   Edge Role
-------------------------------- ---------- ---- ----
SWITCHA                          Gi1/2      Pri  Open
SWITCHB                          Gi1/25          Open
SWITCHB                          Gi1/26          Open
SWITCHC                          Gi1/26          Open
SWITCHC                          Gi1/6           Open
SWITCHA                          Gi1/3      Sec  Alt
```

## Example: REP Fast beacon counters

```
Switch# sh platform rep beacon GigabitEthernet 1/4
Beacon RX : 43984
Beacon TX : 46826
```

## Example: ZTP status transition (upstream)

Before downstream boot: `REP-ZTP Port Status: Blocked`, `REP-ZTP PnP Status: Unknown`.
After downstream boots and requests PnP: `REP-ZTP Port Status: Unblocked`, `REP-ZTP PnP Status: In-Progress`, `REP-ZTP PnP Vlan: 69`.

Syslog from upstream switch:
```
REP-6-ZTPPORTFWD: Interface GigabitEthernet1/2 moved to forwarding on ZTP notification
REP-6-ZTPPORTBLK: Interface GigabitEthernet1/2 moved to blocking on ZTP notification
```

Source: no syslog on the downstream switch because PnP takes control of the console.

Example STP table output:
```
Switch#show platform hardware l2 stp asic-num 0 vlan-id 69 [PnP Vlan]
VlanId:1 StpId:0 MemberPort:3 StpState:FORWARDING
```

## Healthy vs. failure output summary

- Healthy: `Operational Link Status: TWO_WAY`, Role `Open` or one `Alt`, one `Pri` and one `Sec` edge.
- Neighbor missing: `NO_NEIGHBOR`, `Fail No Ext Neighbor`, `Blocked VLAN: 1-4094`.
- Link failed in ring: topology warning with a `Fail` port.
- Auto-discovery off: `REP Segment Id Auto Discovery Status: Disabled`.
