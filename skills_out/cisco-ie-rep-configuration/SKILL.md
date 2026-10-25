---
name: cisco-ie-rep-configuration
description: Configure and troubleshoot Cisco Resilient Ethernet Protocol (REP) segments and rings on Cisco Catalyst IE industrial switches. Use for rep segment setup, edge, primary edge and no-neighbor edge ports, REP admin VLAN (rep admin vlan), VLAN load balancing (rep block port, rep preempt segment, rep preempt delay), STCN (rep stcn), lsl-age-timer, REP Fast (rep fastmode, beacon, copper GE convergence), REP ZTP (rep ztp, rep ztp-enable, PnP startup VLAN, NO_NEIGHBOR), REP Segment-ID Autodiscovery (rep autodisc, rep segment auto), SNMP REP traps, and show rep topology or show interfaces rep detail output. Use when ports show NO_NEIGHBOR, Fail No Ext Neighbor, Fail Logical Open, Alt or unexpected blocked VLANs, or the topology shows a segment failure warning. Not for PTP timing over REP (use the cisco-ie-ptp-over-rep skill).
---

# Cisco IE REP segment configuration and troubleshooting

## When to use

Use this skill when a user asks to:
- Build, change, or remove a REP segment or ring on a Cisco Catalyst IE switch.
- Set edge, primary edge, or no-neighbor edge ports, or the REP admin VLAN.
- Configure or check VLAN load balancing, preemption, STCN, or the LSL age timer.
- Configure or check REP Fast, REP ZTP, or REP Segment-ID Autodiscovery.
- Read `show rep topology` or `show interfaces rep detail` output, or diagnose a port in NO_NEIGHBOR, Fail No Ext Neighbor, Fail Logical Open, or Alt state.
- Migrate a ring from STP to REP, or diagnose REP and STP interaction.

Read these reference files when needed:
- `references/config-commands.md`: full command syntax, parameters, ranges, and rollback for every configuration task. Read before writing any configuration.
- `references/show-output.md`: field meanings and healthy-output examples for every show command. Read when interpreting output.
- `references/feature-rep-fast-ztp-autodisc.md`: REP Fast, REP ZTP, and Segment-ID Autodiscovery prerequisites, limits, and procedures. Read for any of those features.

## Safety rules

1. Confirm the platform, IOS XE release, and ring topology before any change. Ask the user for them. The source gives no command for this; NOT IN SOURCE: verify on device. Features are platform-dependent (for example, REP ZTP is listed for IE3100, IE3200, IE3300, and IE3400, and ZTP on EtherChannel for IE3xxx and IE9300 from IOS XE 17.16.1).
2. Start with read-only show commands. Collect `show rep topology` and `show interfaces rep detail` before changing anything.
3. Before any change, show the exact configuration to be applied and ask the user to confirm. Never apply a change on assumption.
4. Every change block must have a rollback block. Where the source gives no removal command, the block says so.
5. Configure both ends of each link consistently. A segment ID, LSL age timer, or REP Fast setting that differs between neighbors causes failures.
6. Enter REP changes only on the primary edge port when the command says so (`rep block port`, `rep preempt delay`). The source says to enter these only on the REP primary edge port.
7. Warn before any change that can disrupt traffic. Source: "Be careful when configuring REP through a Telnet connection." Enabling REP on the management path can drop connectivity because REP blocks all VLANs until a REP interface unblocks them. Use console access for first-time REP on the management path.
8. `rep preempt segment` causes a momentary traffic disruption and prompts for confirmation. Get explicit user approval first.
9. Do not enable `rep fastmode` together with MACsec on IE3x00 Rugged, IE3400 Heavy Duty, or ESS3300 switches. The source says the two cannot coexist.

## Configuration procedure

Run this sequence for a new REP segment. Each step is in `references/config-commands.md`.

1. Check platform and release (see Safety rule 1).
2. Read current state: `show rep topology` and `show interfaces rep detail` (read-only).
3. Optional: set the REP admin VLAN. Default is VLAN 1. Range 2 to 4094. Use one admin VLAN for all segments on the switch. Confirm the VLAN is not the RSPAN VLAN.
4. Configure the trunk and segment on each segment interface, starting with one port and extending to contiguous ports to minimize segment count. REP ports must be Layer 2 trunk ports. Use the same allowed VLAN set on all REP trunk ports.
5. Configure exactly two edge ports per segment, one of them `primary`. The source says an edge port without `primary` is the secondary edge port.
6. Optional: configure VLAN load balancing on the primary edge port with `rep block port`, and a trigger with `rep preempt delay` (15 to 300 seconds) or the manual `rep preempt segment` command.
7. Optional: configure `rep lsl-age-timer`, `rep stcn`, SNMP traps, REP Fast, REP ZTP, or REP Segment-ID Autodiscovery.
8. Verify (see Verification).
9. Save: `copy running-config startup-config` (optional step in source).

Only the REP segment, trunk mode, and edge configuration are mandatory. Optional features can be added in any order after the segment works.

## Verification

Run these read-only checks after any change:

```
show rep topology
show rep topology segment <segment-id>
show interfaces rep detail
show interfaces <interface-id> rep detail
```

Healthy results for a fully operational segment:
- Exactly one port with Role `Alt` in the segment (for each VLAN, when no load balancing is configured).
- Every other port shows Role `Open`.
- Exactly one port marked `Pri` and one marked `Sec` in the Edge column.
- Each regular port shows `Operational Link Status: TWO_WAY`.

Source: "A segment with all ports operational has one port (referred to as the ALT port) in the blocked state for each VLAN."

If anything is different, go to Troubleshooting. Check `show platform rep beacon interface <interface-id>` only when REP Fast is configured.

## Troubleshooting (symptom -> checks -> cause -> fix)

The source does not rank causes by likelihood. The order below follows the source's own order of conditions. NOT IN SOURCE: likelihood ranking.

### 1. Port shows NO_NEIGHBOR, Fail No Ext Neighbor, or all VLANs blocked (Blocked VLAN 1-4094)

Checks:
- `show interfaces <interface-id> rep detail`: read Operational Link Status, Segment-id, and Port Role.
- Confirm the neighbor port uses the same segment ID.
- Confirm exactly one neighbor on the link carries that segment ID.
- Confirm the link is a Layer 2 trunk.

Causes and fixes:
- No neighbor has the same segment ID. The source says a segment port does not become operational without a neighbor carrying the same ID. Fix: configure the same `rep segment <segment-id>` on the neighbor (see config-commands.md). Confirm with the Verification commands.
- More than one neighbor has the same segment ID. Fix: correct the cabling or the segment IDs so only one neighbor matches.
- Neighbor does not acknowledge the local port as a peer. NOT IN SOURCE: specific acknowledgment troubleshooting. Escalate: collect `show logging` and `show tech-support` (the source lists both as recommended checks).
- Expected during PnP provisioning. A downstream switch not yet configured for REP shows NO_NEIGHBOR by design. See the REP ZTP section in feature-rep-fast-ztp-autodisc.md before treating this as a fault.

### 2. Topology shows Fail No Ext Neighbor on one port and Fail Logical Open on another

Cause: more than two ports failed in a segment with no external neighbors configured. Source: one port goes to forwarding for data-path connectivity and is labeled Fail Logical Open; the other shows Fail No Ext Neighbor.

Fix: configure the external neighbors for the failed ports. The ports then move through alternate-port transitions and settle to Open or Alt.

### 3. VLAN load balancing is not happening

Checks:
- `show interfaces rep detail` on the primary edge port: read `Preempt Delay Timer`, `Configured Load-balancing Block Port`, and `Configured Load-balancing Block VLAN`.
- `show rep topology segment <segment-id>`.

Causes:
- Trigger not run. Source default is manual preemption with no delay. Fix: run `rep preempt segment <segment-id>` (confirm disruption first), or configure `rep preempt delay`.
- Segment not terminated by an edge port on each end. Source: load balancing requires two edge ports in the segment. Fix: configure both ends as edge ports.
- Load balancing configured on a non-primary port. Source: enter `rep block port` only on the primary edge port.
- Offset 1 used as the alternate port. Source: the primary edge port has offset 1, so offset 1 is invalid for `rep block port`.

### 4. show rep topology prints "REP detects a segment failure, topology may be incomplete" with a Fail port

Cause: a link in the segment failed. Source: the topology view can be incomplete after a failure, and this limitation affects only the `show rep topology` output; the data path is not affected by it. NOT IN SOURCE: whether the data path is fully healthy in every failure case.

Checks: find the port with Port Role `Fail` in `show interfaces rep detail`. Check the physical link on that port.

Fix: restore the failed link. REP manages a single port failure. Source: "Multiple port failures within the REP segment cause loss of network connectivity."

Escalate if more than one port in the segment has failed.

### 5. Connectivity lost after REP was enabled

Cause: REP blocks all VLANs until a REP peer unblocks them. Source warns this can drop management access when REP is enabled over the same path.

Checks: confirm the switch was accessed through a path that REP now blocks. Confirm `rep admin vlan` and the trunk VLAN set.

Fix: restore access through console. NOT IN SOURCE: recovery steps beyond console access. Plan REP changes over console for management paths.

### 6. Ring convergence is slow on copper Gigabit Ethernet

Cause: source gives copper GE link down detection of 750 to 350 ms, compared with 10 ms on fiber. Source recommends REP Fast for Gigabit copper, and `lsl-age-timer` for other cases.

Checks:
- `show platform rep beacon interface <interface-id>`: confirm Beacon RX and TX counters increase.
- Confirm `rep fastmode` is configured on both ends of the link.
- Confirm no more than six interfaces on the node use REP Fast.
- Confirm MACsec is not configured on the same link.

Fix: configure `rep fastmode` on both ends. Remove the excess REP Fast configuration. Remove MACsec or use normal REP. See feature-rep-fast-ztp-autodisc.md.

### 7. Link flaps with lsl-age-timer configured

Causes:
- The two ends have different `rep lsl-age-timer` values. Source: both ports must match to avoid link flaps.
- The value is below 1000 ms on an EtherChannel port channel. Source: rejected with an error.

Fix: set the same value on both ends, at least 1000 ms on port channels. For copper GE, use REP Fast instead.

### 8. Autodiscovery does not learn a segment ID

Checks: `show interfaces rep detail` and read:
- `REP Segment Id Auto Discovery Status` (should be Enabled).
- `REP Segment Id Type` (Auto when learned, Manual when static).

Causes (source-derived):
- Autodiscovery disabled globally. Fix: `rep autodisc` (see config-commands.md).
- Interface not configured with `rep segment auto`.
- The edge device has no static segment ID. Source: primary and secondary edges need `rep segment <segment_id> edge`.
- Autodiscovery depends on CDP. Source: EtherChannel links are not supported.
- Downlink partner ports are not paired automatically. Source: configure each downlink separately.
- Inserting an edge node into an existing segment is not supported. Configure static segment IDs on edge devices.
- Wiring error: new switch connected to the wrong interface of an existing switch. Source: leads to segment failure. Fix: reconnect to the interfaces that carry the same segment ID.
- Previously removed `rep segment auto` config. Source: after removal with `no rep segment` or an overwrite, shut down the interface, bring it up, then re-enter `rep segment auto`.

### 9. REP ZTP port stays Blocked or PnP does not start

Checks:
- `show interfaces rep detail` on the upstream interface: read `REP-ZTP Status`, `REP-ZTP PnP Status`, `REP-ZTP PnP Vlan`, and `REP-ZTP Port Status`.
- Syslog on the upstream switch: look for `REP-6-ZTPPORTFWD` (moved to forwarding on ZTP notification) and `REP-6-ZTPPORTBLK` (moved to blocking).
- `show platform hardware l2 stp asic-num 0 vlan-id <pnp-vlan>` to check the PnP startup VLAN state.
- Optional debug, from source: `debug rep lslsm` and `debug rep packet`.

Causes:
- `rep ztp-enable` not configured on the upstream interface. Source: it is disabled by default at interface level.
- Upstream or downstream switch does not support REP ZTP. Source: both must support the feature.
- Configuration template from Catalyst Center or PnP server omits the CLI. Source: the user must add the CLI explicitly to the template.
- Failure in the PnP process. Source: the upstream interface reverts to blocking the PnP startup VLAN. NOT IN SOURCE: further PnP troubleshooting. Escalate.

Fix: configure `rep ztp-enable` on the upstream interface (see config-commands.md). Confirm the downstream switch sends the ZTP TLV.

### 10. Ring with more than 20 nodes does not converge below 50 ms

Cause: source says ring sizes greater than 20 nodes may not achieve sub-50 ms convergence.

Fix: reduce the ring size, or accept the slower convergence. Source does not give a fix beyond this. NOT IN SOURCE: alternative designs.

### 11. Downstream STP network causes a loop

Cause: an STP connection that is not at the segment edge. STP does not run on REP segments and all STP BPDUs are dropped at REP interfaces.

Fix: move the STP connection to the segment edge. Configure the edge port with `rep stcn stp` only if STCNs must reach STP networks. Source: the edge no-neighbor node needs MST mode when `rep stcn stp` is configured.

### 12. Port converted from REP to STP does not forward

Cause: a segment port reconfigured as an STP port restarts according to the STP configuration. Default is designated blocking. Source: it goes to forwarding only if PortFast is configured or STP is disabled.

Fix: check STP configuration on the interface. NOT IN SOURCE: STP tuning beyond this.

### 13. Segment or switch limits exceeded

Source limits:
- Maximum 26 REP segments per switch.
- Only two ports on a switch may belong to the same segment.
- REP ZTP or Segment-ID Autodiscovery limits a single node to three REP segments.

Fix: reduce segments or move ports. Escalate if the design needs more segments.

## Reference files

- `references/config-commands.md`: read before writing any configuration. Covers admin VLAN, REP segment, edge ports, STCN, block port, preempt delay, LSL age timer, REP Fast, REP ZTP, Autodiscovery, SNMP traps, and manual preemption, each with syntax, ranges, defaults, and rollback.
- `references/show-output.md`: read when interpreting output. Covers field meanings for `show interfaces rep detail` and `show rep topology`, and port roles and states.
- `references/feature-rep-fast-ztp-autodisc.md`: read for REP Fast, REP ZTP, or Segment-ID Autodiscovery work. Covers prerequisites, limits, and procedures for each feature.
