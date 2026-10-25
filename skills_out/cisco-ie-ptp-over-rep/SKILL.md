---
name: cisco-ie-ptp-over-rep
description: Configure and troubleshoot PTP (Precision Time Protocol) over Cisco REP rings on Cisco Catalyst IE industrial switches. Use for ptp profile power, ptp profile default, ptp mode p2ptransparent, ptp mode e2etransparent, ptp domain, ptp vlan, PTP VLAN binding on REP ring-facing ports, boundary clock vs transparent clock choice, PTP accuracy per hop, PTP over REP with REP Fast, and timing loss after REP reconvergence. Use when a user asks about time synchronization in a REP ring, IEEE 1588 v2 on IE9300 or IE3500 switches, or power profile 2011 or 2017 on REP. Not for basic REP segment setup (use the cisco-ie-rep-configuration skill).
---

# Cisco IE PTP over REP

## When to use

Use this skill when a user asks to:
- Synchronize PTP clocks across a REP ring or linear REP topology.
- Choose or configure the default profile (IEEE 1588 v2) or the power profile (2011 or 2017).
- Set PTP mode (`ptp mode e2etransparent` or `ptp mode p2ptransparent`) and PTP domain.
- Bind PTP to a VLAN on REP ring-facing interfaces (`ptp vlan`).
- Understand PTP accuracy per hop, or the effect of a timing path using copper SFPs.
- Diagnose timing loss or drift after a REP topology change.

This skill has no reference files. All procedures and limits are in this file. For REP segment setup, configuration syntax, show command meanings, and REP troubleshooting, use the `cisco-ie-rep-configuration` skill.

## Safety rules

1. Confirm the platform first. Source platforms: Cisco Catalyst IE9300 Rugged Series Switch and Cisco IE3500 Rugged Series Switch. The source gives no command for checking platform or release. NOT IN SOURCE: verify on device.
2. Confirm REP is already configured and the ring shows the expected roles in `show rep topology` before PTP changes. Source lists "Ensure REP is configured" as a prerequisite and recommends checking the topology first.
3. Show the exact configuration to be applied and get confirmation before changing PTP. PTP configuration changes can disturb timing on live devices.
4. Each change block needs a rollback block. The source gives clearing steps in the procedures (`no ptp profile`, `no ptp mode`, `no ptp domain`). Rollback for `ptp vlan` and `no shutdown` on the VLAN is NOT IN SOURCE in those exact forms. Verify on device.
5. Use only one PTP profile at a time. The procedures clear the existing profile first.
6. Do not mix standard REP and REP Fast in the same PTP over REP ring. Source: configure REP Fast on all ports in the ring if using REP Fast.
7. Do not rely on PTP accuracy values when GLC-T copper SFP transceivers are in the timing path. Source: they can reduce PTP timing accuracy.

## Platform and limits (source)

| Platform | PTP clock types | PTP profiles | Accuracy | REP rings |
|---|---|---|---|---|
| Cisco Catalyst IE9300 Rugged | Boundary Clock, Grandmaster Boundary Clock, Transparent Clock | Default (IEEE 1588 v2), Power 2011/2017 | BC ~200 ns per hop; TC ~50 ns per hop | Up to 26 open REP segments |
| Cisco IE3500 Rugged | Boundary Clock, Grandmaster Boundary Clock, Transparent Clock | Default Profile (IEEE 1588 v2), Power Profile 2011/2017 | BC ~200 ns per hop; TC < 200 ns per hop | Up to 26 open REP segments or 3 REP Fast segments |

General capabilities (source):
- PTP clocks: Boundary Clock, Grandmaster Clock, Transparent Clock.
- PTP profiles: Default (IEEE 1588 v2), Power Profile 2011/2017 (platform dependent).
- Media: copper ports and SFP-based fiber ports.
- REP topology: ring, linear, up to 26 REP rings.
- VLAN behavior: Boundary Clocks support multiple VLANs. Transparent Clocks support a single VLAN.

Guidelines (source):
- Each Boundary Clock adds delay at every hop.
- Transparent Clock deployments minimize per-hop delay. TCs are limited to a single VLAN.
- End-to-end accuracy depends on the cumulative delay from the number of BCs and TCs in the path.
- PTP over REP supports standard REP and REP Fast. Do not mix them in the same ring.

## Configuration procedure

Common prerequisite: REP segment configured (see cisco-ie-rep-configuration skill).

### A. Common REP and VLAN setup (both profiles)

1. Create the VLAN that serves as both the REP admin VLAN and the PTP transport VLAN (source). Optionally clear a local shutdown state.

```
Switch# configure terminal
Switch(config)# vlan 100
Switch(config-vlan)# no shutdown
Switch(config-vlan)# exit
```

Source note: `no shutdown` applies to the Layer 2 VLAN, not an SVI. It clears a locally shut (`lshut`) state in brownfield networks.

Rollback: NOT IN SOURCE for VLAN removal in this procedure. Verify on device before deleting a VLAN.

2. Configure REP and trunk mode on each ring-facing interface.

```
Switch(config)# interface GigabitEthernet1/3
Switch(config-if)# switchport mode trunk
Switch(config-if)# switchport trunk allowed vlan 100
Switch(config-if)# rep segment 10
Switch(config-if)# exit
Switch(config)# interface GigabitEthernet1/4
Switch(config-if)# switchport mode trunk
Switch(config-if)# switchport trunk allowed vlan 100
Switch(config-if)# rep segment 10
Switch(config-if)# exit
```

Rollback per interface:
```
Switch(config)# interface GigabitEthernet1/3
Switch(config-if)# no rep segment
Switch(config-if)# exit
```

Notes:
- The source's step for this part also lists a `no shutdown` example in which the command shown is `rep segment 10` (a source error). NOT IN SOURCE: the intended interface command. Verify on device before using `no shutdown` on the interface.
- Rollback for `switchport trunk allowed vlan`: NOT IN SOURCE. Verify on device.

3. Set the REP admin VLAN.

```
Switch(config)# rep admin vlan 100
```

Rollback (returns to default VLAN 1):
```
Switch(config)# no rep admin vlan
```

4. Verify REP before PTP.

```
Switch# show rep topology
```

Expected: one `Pri` edge, one `Sec` edge, one `Alt` port, others `Open`. See Verification below for the source example.

### B. Power profile (Power 2011/2017)

1. Clear existing PTP configuration.

```
Switch# configure terminal
Switch(config)# no ptp profile
Switch(config)# no ptp mode
Switch(config)# no ptp domain
```

Rollback: these commands are the clearing step itself. To restore a prior profile, reconfigure it from its own procedure. NOT IN SOURCE: a full rollback of PTP profile state.

2. Complete section A, step 2 (REP on the ring-facing interfaces). Then exit to global configuration mode.

3. Configure the PTP power profile, mode, and domain.

```
Switch(config)# ptp profile power
Switch(config)# ptp mode p2ptransparent
Switch(config)# ptp domain 100
```

Source notes that `ptp mode p2ptransparent` configures a peer-to-peer Transparent Clock under the power profile.

Rollback:
```
Switch(config)# no ptp domain
Switch(config)# no ptp mode
Switch(config)# no ptp profile
```

4. Bind PTP to the VLAN on each REP ring-facing interface.

```
Switch(config)# interface GigabitEthernet1/3
Switch(config-if)# ptp vlan 100
Switch(config-if)# exit
Switch(config)# interface GigabitEthernet1/4
Switch(config-if)# ptp vlan 100
Switch(config-if)# exit
```

Rollback per interface: NOT IN SOURCE: `no ptp vlan` syntax. Verify on device.

5. Verify with `show rep topology` (see Verification).

### C. Default profile (IEEE 1588 v2, end-to-end transparent clock)

1. Clear existing PTP configuration.

```
Switch# configure terminal
Switch(config)# no ptp profile
Switch(config)# no ptp mode
Switch(config)# no ptp domain
```

2. Configure REP on each interface.

```
Switch(config)# interface GigabitEthernet1/3
Switch(config-if)# rep segment 10
Switch(config-if)# exit
```

Repeat for the second interface. Rollback: `no rep segment` on each interface.

3. Configure PTP mode and domain.

```
Switch(config)# ptp mode e2etransparent
Switch(config)# ptp domain 100
Switch(config)# end
```

Rollback:
```
Switch(config)# no ptp domain
Switch(config)# no ptp mode
```

Note: the source's default-profile procedure has no `ptp vlan` step. NOT IN SOURCE: whether `ptp vlan` is required with the default profile. Verify on device.

4. Verify with `show rep topology` (see Verification).

## Verification

Use the REP topology check after every PTP change:

```
show rep topology
```

Expected healthy output: one Pri, one Sec, one Alt, others Open.

Source example:
```
Switch# show rep topology
REP Segment 10
BridgeName       PortName   Edge Role
---------------- ---------- ---- ----
RB1              Gi4/0/7    Pri  Open
Switch           Gi1/3           Open
Switch           Gi1/4           Open
RB2              Gi2/0/7         Open
RB2              Gi2/0/8         Alt
LANB-1           Gi1/4           Open
LANB-1           Gi1/3           Open
RB1              Gi4/0/8    Sec  Open
```

PTP status commands: NOT IN SOURCE. The source gives no PTP show commands. Verify on device for PTP lock state, offset, and per-port timing.

## Troubleshooting (symptom -> checks -> cause -> fix)

The source gives no PTP-specific troubleshooting. Items below derive from source limits and recommended practices. Causes are not ranked by likelihood. NOT IN SOURCE: likelihood ranking.

### 1. Timing accuracy worse than the stated per-hop values

Checks:
- Count BCs and TCs in the timing path. Source: accuracy depends on cumulative delay.
- Check for GLC-T copper SFP transceivers in the timing path. Source: these can reduce accuracy.

Fix:
- Reduce the BC count, or use TCs where the topology allows (TCs are limited to a single VLAN).
- Replace GLC-T copper SFPs in the timing path with fiber. NOT IN SOURCE: specific SFP recommendations.

### 2. Timing drifts after a REP link failure or reconvergence

Causes (source):
- PTP adjustments overlap with REP reconvergence.
- Insufficient stabilization time after topology changes.
- CPU-intensive events during reconvergence (high-frequency SNMP polling, PTP bursts, BFD sessions).

Checks:
- `show processes cpu sorted`
- `show platform resource`
- `show logging` for CPUHOG events linked to REP or control-plane processes.

Fix:
- Allow stabilization time after a topology change. NOT IN SOURCE: duration.
- Avoid planned PTP changes during REP reconvergence.
- Reduce SNMP polling frequency and other CPU-intensive load. Source: configure monitoring tools and SNMP polling conservatively.
- Escalate: collect `show tech-support`.

### 3. PTP not synchronized across a ring that mixes REP and REP Fast

Cause: source says do not mix standard REP and REP Fast within the same ring.

Fix: configure REP Fast on all ports in the ring, or use standard REP on all ports. Plan a maintenance window for the change (source: plan changes carefully to avoid simultaneous control-plane events).

### 4. Transparent clock VLAN issue

Cause: TCs support a single VLAN only (source). A transparent clock configured for multiple VLANs does not meet the source's VLAN rule.

Fix: limit the TC to one VLAN. Use a Boundary Clock if multiple VLANs are required (source: BCs support multiple VLANs).

### 5. Too many REP rings for the PTP design

Cause: source limit of up to 26 REP rings (up to 26 open REP segments on IE9300; up to 26 open or 3 REP Fast segments on IE3500).

Fix: reduce the ring count. Source recommends designing REP rings only where operationally necessary.

### 6. PTP profile change not taking effect

Cause (source): the procedures require clearing the old profile before applying a new one (`no ptp profile`, `no ptp mode`, `no ptp domain`).

Fix: clear all three, then reapply the new profile's configuration in order. Check with `show rep topology` after changes.

### 7. PTP on a REP ring-facing port not carrying timing

Checks:
- Confirm `ptp vlan <vlan-id>` is set on the ring-facing interface (power profile procedure).
- Confirm the VLAN is created and not shut (`no shutdown` in VLAN mode, per source).
- Confirm `switchport trunk allowed vlan <vlan-id>` includes the PTP VLAN (source says PTP messages must not be blocked by VLAN filtering).
- Confirm `rep admin vlan` matches the PTP VLAN (source says REP control messages and PTP packets share the same VLAN).

Fix: correct the item that does not match. NOT IN SOURCE: additional PTP diagnostics.

Escalate if the above are correct and timing still does not lock. NOT IN SOURCE: PTP lock troubleshooting commands.
