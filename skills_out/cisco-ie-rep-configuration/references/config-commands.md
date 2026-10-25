# REP configuration commands

Every command below is copied from the source. Items marked NOT IN SOURCE have no documented removal command. Verify on device before using them.

## 1. REP admin VLAN (optional, global)

Range 2 to 4094. Default is VLAN 1. Cannot be the RSPAN VLAN. One admin VLAN for all segments on the switch.

```
Device> enable
Device# configure terminal
Device(config)# rep admin vlan 2
Device(config)# end
```

Verify:
```
Device# show interface gigabitethernet1/1 rep detail
```

Rollback (restores default VLAN 1):
```
Device# configure terminal
Device(config)# no rep admin vlan
Device(config)# end
```

## 2. Configure a REP interface (mandatory)

Each segment interface must be a Layer 2 trunk and must have a segment ID. Segment ID range is 1 to 1024. Two edge ports, one primary, are required per segment.

```
Device# configure terminal
Device(config)# interface gigabitethernet1/1
Device(config-if)# switchport mode trunk
Device(config-if)# rep segment 1 edge no-neighbor primary
Device(config-if)# end
```

Syntax:
```
rep segment segment-id [edge [no-neighbor]  [primary ]] [preferred ]
```

Parameters:
- `segment-id`: 1 to 1024.
- `edge`: makes the port an edge port. Without `primary`, it is the secondary edge port.
- `no-neighbor`: edge port with no external REP neighbor. Inherits all edge port properties.
- `primary`: the primary edge port, where VLAN load balancing is configured.
- `preferred`: preferred alternate port for VLAN load balancing. Source: does not guarantee the port becomes the alternate port; it gives a slight edge over equal contenders.

Notes from source:
- Two edge ports are required per segment.
- Two edge ports on different switches can both carry `primary`. REP selects one. Check with `show rep topology`.
- If only one port on a switch is in a segment, it should be an edge port.
- If two ports on one switch are in the same segment, they must both be edge ports, both regular, or one regular and one edge no-neighbor. An edge port and a regular port on the same switch cannot be in the same segment.
- REP is supported on EtherChannels, but not on an individual member port.

Rollback for the interface segment (documented in the Segment-ID Autodiscovery section; the REP interface section gives no separate removal command):
```
Device# configure terminal
Device(config)# interface gigabitethernet1/1
Device(config-if)# no rep segment
Device(config-if)# end
```

## 3. STCN (optional, edge port)

Sends segment topology change notices from the edge port.

```
Device(config-if)# rep stcn segment 25-50
```

Syntax:
```
rep stcn {interface  interface id | segment  id-list  | stp }
```

Parameters:
- `interface interface-id`: a physical interface or port channel to receive STCNs.
- `segment id-list`: one or more segments, range 1 to 1024.
- `stp`: send STCNs to STP networks.

Note: MST mode is required on edge no-neighbor nodes when `rep stcn stp` is used.

Rollback: NOT IN SOURCE: removal command for `rep stcn`. Verify on device.

## 4. VLAN load balancing (optional, primary edge port only)

Source: "Enter this command only on the REP primary edge port."

```
Device(config-if)# rep block port id 0009001818D68700 vlan 1-100
```

Syntax:
```
rep block port {id  port-id  | neighbor-offset  | preferred } vlan  {vlan-list  | all }
```

Parameters:
- `id port-id`: alternate port by port ID. Get port IDs with `show interface <type number> rep [detail]`.
- `neighbor_offset`: range -256 to 256. Positive values are downstream of the primary edge port. Negative values are downstream of the secondary edge port. Value 0 is invalid. Offset 1 is invalid (primary edge port). Use -1 for the secondary edge port.
- `preferred`: uses the regular port previously set as preferred.
- `vlan vlan-list`: one VLAN or a range.
- `vlan all`: block all VLANs.

Rollback: NOT IN SOURCE: removal command for `rep block port`. Verify on device.

## 5. Preemption delay (optional, primary edge port only)

Triggers VLAN load balancing automatically after link failure and recovery. Range 15 to 300 seconds. Default is manual preemption with no delay. Source: the delay timer restarts if another port fails before it elapses.

```
Device(config-if)# rep preempt delay 100
```

Rollback: NOT IN SOURCE: removal command. Default is manual preemption with no delay, per source. Verify on device.

## 6. Manual VLAN load-balancing trigger (privileged EXEC)

Use when no preempt delay is configured. Complete all segment configuration first. Causes a momentary traffic disruption and requires confirmation.

```
Switch# rep preempt segment 100
The command will cause a momentary traffic disruption.
Do you still want to continue? [confirm]
```

Optional verification:
```
Switch# show rep topology segment 100
```

This is an action, not a configuration. Rollback: NOT IN SOURCE.

## 7. LSL age timer (optional, interface)

Time in milliseconds that the interface stays up without a hello from a neighbor. Range 120 to 10000 ms in 40 ms increments. Default 5000 ms. Hello timer is the age value divided by 3.

```
Device(config-if)# rep lsl-age-timer 2000
```

Notes:
- EtherChannel port channels do not support values below 1000 ms.
- Both ports on the link must use the same value to avoid link flaps.
- Source says it is for cases where normal link down detection is too slow. FastEthernet and fiber do not need it. Gigabit copper can use REP Fast.

Rollback: NOT IN SOURCE: removal command. The documented default is 5000 ms. Verify on device before restoring it.

## 8. REP Fast (optional, interface, both ends)

```
Switch# configure terminal
Switch(config)# int gi 1/4
Switch(config-if#) rep segment 1 edge
Switch(config-if)# rep fastmode
Switch(config-if)# end
Switch# sh run int gi 1/4
interface GigabitEthernet1/4
switchport trunk allowed vlan 1-10
switchport mode trunk
rep segment 1 edge
rep fastmode
```

Prerequisite: REP is enabled and the topology is configured.

Rollback: the source tells the user to "disable fast mode on both ends of the link" but does not show the command. NOT IN SOURCE: `no rep fastmode` syntax. Verify on device before using.

Source example line `Switch(config-if#) rep segment 1 edge` contains a typo (`config-if#`). Use the prompt that matches the device.

## 9. REP ZTP (global and interface)

Defaults: global enabled, interface disabled. Interface command must be set on the upstream interface connected to the downstream switch. Source: when applying configuration from Catalyst Center or a PnP server, the CLI must be explicitly included in the template.

Global:
```
Switch# configure terminal
Switch(config)# rep ztp
```

Global rollback:
```
Switch(config)# no rep ztp
```

Interface:
```
Switch(config)# interface <interface-name>
Switch(config-if)#rep ztp-enable
```

Interface rollback:
```
Switch(config-if)#no rep ztp-enable
```

Minimum example from source:
```
Switch#show running-config interface gigabitEthernet 1/2
interface GigabitEthernet1/2
 switchport mode trunk
 rep segment 100
 rep ztp-enable
end
```

Rollback for the example's interface segment: see section 2.

## 10. REP Segment-ID Autodiscovery

Enabled by default. Re-enable on the switch upstream and downstream interfaces as needed.

Global enable:
```
switch(config)#rep autodisc
```

Global disable:
```
switch(config)#no rep autodisc
```
Rollback to re-enable: `rep autodisc`.

Edge device must have a static segment ID before autodiscovery is used:
```
rep segment <segment_id> edge
```

Interface, learn segment ID (upstream example from source):
```
switch(config)#int gig1/1
switch(config-if)#rep seg auto
```

Source example config (upstream):
```
switch#show running-config interface gigabitEthernet 1/3
interface GigabitEthernet1/3
 switchport mode trunk
 rep segment auto 1
```

Source example config (downstream):
```
switch#show running-config interface gigabitEthernet 1/2
interface GigabitEthernet1/2
 switchport mode trunk
 rep segment auto
end
```

Disable learning on an interface:
```
switch(config-if)#no rep segment
```

Notes:
- Uplinks Gi1/1 and Gi1/2 are paired automatically on IOS XE Cupertino 17.9.1 and later. Downlinks are not paired; configure each downlink separately.
- After `no rep segment` or an overwrite with `rep segment \`, you cannot re-enable `rep segment auto` directly. Shut down the interface, bring it up, then enter `rep segment auto`.
- Feature depends on CDP. Not supported on EtherChannel.

Rollback to remove autodiscovery from one interface: `no rep segment` (see above). Global rollback: `no rep autodisc`.

## 11. Other documented commands

SNMP REP traps (global):
```
Switch(config)# snmp mib rep trap-rate 500
```
Range 0 to 1000 traps per second. Default 0 means no limit, a trap is sent at every occurrence.

Rollback: NOT IN SOURCE: no-form syntax. Setting the documented default `0` restores the default. Verify on device.

Save configuration (optional, source):
```
Device# copy running-config startup-config
```

Show running config (source):
```
Switch# show running-config
```
