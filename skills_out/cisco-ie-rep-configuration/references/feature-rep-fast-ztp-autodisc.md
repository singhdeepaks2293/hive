# REP Fast, REP ZTP, and REP Segment-ID Autodiscovery

## REP Fast

What it does (source): speeds link failure detection on copper Gigabit Ethernet ports using a beacon mechanism. Detection within 5 to 10 ms. Convergence within 50 ms when the neighbor supports REP Fast and acknowledges.

How it works (source): two timers per interface. The first sends a beacon frame every 3 ms. If the frame is not received after transmission, the second timer checks reception within 10 ms. If not received, a link-down message is sent.

Prerequisites and rules:
- Both ends of the link must support and enable REP Fast. Source: "REP Fast requires both ends of the link to support REP Fast to work."
- Works per link. Does not change the REP protocol or ring size.
- A ring can mix normal REP links and REP Fast links.
- Interfaces with REP Fast transmit 3000 packets per second as normal operation.
- Only six interfaces on a single REP node can be configured for REP Fast at a time.
- If the neighbor does not support REP Fast, disable fast mode on both ends and use normal REP. NOT IN SOURCE: the exact command. Verify on device.
- Cannot coexist with MACsec on IE3x00 Rugged, IE3400 Heavy Duty, and ESS3300 switches. Beacons are dropped and MKA cannot negotiate. MACsec with normal REP works.
- Source feature history: REP Fast initial support on IE3200, IE3300, and IE3400. Not supported on IE3100.
- PTP over REP: do not mix REP and REP Fast within the same ring. Configure REP Fast on all ports in the ring.

Procedure: see config-commands.md section 8.

Verification:
```
show platform rep beacon interface <interface-id>
show interfaces <interface-id> rep detail
```

## REP Zero Touch Provisioning (REP ZTP)

Purpose (source): lets new IE switches join an existing REP ring with no manual configuration by unblocking only the PnP startup VLAN on the upstream interface. PnP does not support REP by design, so REP ZTP adds a TLV to REP LSL packets.

Process (source):
1. The new downstream switch has no startup config and starts PnP/autoinstall.
2. The upstream interface is in REP NO_NEIGHBOR state and blocks all VLANs, including the PnP startup VLAN.
3. The downstream switch sends REP LSL packets carrying the REP ZTP TLV to request PnP startup VLAN unblock.
4. The upstream switch, if `rep ztp-enable` is configured on that interface, unblocks only the PnP startup VLAN.
5. Downstream switch completes PnP and configures its own interface for REP.
6. Upstream reverts to blocking the PnP startup VLAN once the downstream switch is configured for REP. If PnP fails, it also reverts to blocking.

Requirements and limits:
- Both upstream and downstream switches must support REP ZTP.
- Requires PnP on IE3100, IE3200, IE3300, and IE3400 switches (source).
- From IOS XE 17.16.1, supported on IE3100 physical interfaces and EtherChannels.
- ZTP on EtherChannel is supported on IE3xxx and IE9300 from IOS XE 17.16.1.
- Supported on copper (downlink) and fiber (uplink) interfaces.
- Interoperable only with other IE switches running IOS XE that claim REP ZTP support.
- An upstream switch can be part of multiple REP rings and connect to multiple downstream neighbors. The PnP startup VLAN unblocks only on interfaces where a downstream switch connects.
- REP behavior in NO_NEIGHBOR state changed starting IOS XE 17.8.1 to let DHCP requests reach the server. Source: no impact to the REP state machine after PnP completes. Changes apply only to REP ZTP.
- REP ZTP and REP Segment-ID Autodiscovery each limit a single node to three REP segments (source).
- Security: the upstream interface must be explicitly enabled with `rep ztp-enable`.
- Global default enabled, interface default disabled.

Procedure: see config-commands.md section 9.

Verification: see show-output.md, ZTP example.

Deprecation (source): REP-Negotiated is deprecated starting IOS XE 17.17.1, 17.15.3, 17.12.5, and 17.9.7. Use REP-ZTP as the alternative. Do not configure REP-Negotiated on new deployments.

Common failure modes: see SKILL.md, troubleshooting item 9.

## REP Segment-ID Autodiscovery

Purpose (source): lets a switch learn and retain segment IDs automatically from neighbors (via CDP), reducing manual configuration and segment ID mismatches.

Supported platforms (source): Cisco Catalyst IE3x00 Rugged, IE3400 Heavy Duty, and ESS3300 Series, from IOS XE Cupertino 17.9.x.

Scenarios (source):
- Add a new switch to an existing segment. Enable `rep autodisc` on existing upstream and downstream interfaces, then `rep segment auto` on the new switch interfaces.
- Build a new closed segment. Start with static segment ID on both primary and secondary edges, which are on the same switch. Add the next node and insert nodes between them.
- Build a new open segment. Start with static segment ID on both primary and secondary edges, on different switches.
- Build a daisy-chain segment with uplinks. Start with static segment ID on the edge node. Connect the next device to an uplink on the edge. Enable autodiscovery on that uplink. Repeat.

Limitations (source):
- Only uplink ports Gi1/1 and Gi1/2 pair automatically. No predefined pairing for downlinks; configure each downlink separately.
- Not supported when inserting an edge node into an existing segment. Configure static IDs on edge devices.
- New switches inserted between existing segment switches must connect to the existing interfaces that carry the same segment ID. Wrong connections cause segment failure.
- After `no rep segment` or an overwrite with `rep segment \`, shut down the interface, bring it up, and then re-enter `rep segment auto`.
- Depends on CDP. Not supported on EtherChannel.

Procedure: see config-commands.md section 10.

Verification:
```
show interfaces rep detail
```
Check:
- `REP Segment Id Auto Discovery Status: Enabled` (global)
- `REP Segment Id Type: Auto` (learned) or `Manual` (static)
