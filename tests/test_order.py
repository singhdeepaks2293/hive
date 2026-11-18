from queen import Signal, SignalKind
from queen.models import DeviceDomains

from .helpers import make_queen, map_device, run


def syslog(device, mnemonic, **payload):
    return Signal(kind=SignalKind.SYSLOG, device=device, mnemonic=mnemonic, severity=4,
                  payload=payload)


def test_ring_failure_seen_by_many_switches_is_one_incident():
    async def go():
        q, board, clock, _ = make_queen()
        for dev in ("ie-sw1", "ie-sw2", "ie-sw3"):
            await map_device(q, dev, **{"rep-segment": "1"})
            await q.ingest(syslog(dev, "REP-4-LINKSTATUS"))
        await q.tick()
        incs = await q.incidents()
        assert [i.key for i in incs] == ["fault:rep-segment:1"]
        assert sorted(incs[0].devices) == ["ie-sw1", "ie-sw2", "ie-sw3"]
        assert "tshoot.rep" in incs[0].skills
    run(go())


def test_link_down_on_a_ring_port_joins_the_ring_incident():
    async def go():
        q, board, clock, _ = make_queen()
        await board.put("domains", "ie-sw2", DeviceDomains(
            device="ie-sw2", domains={"rep-segment": "1"}, ports={"Gi1/0/1": "rep-segment:1"},
            mapped_by="t", mapped_at=clock()))
        await q.ingest(syslog("ie-sw2", "REP-4-LINKSTATUS"))
        await q.ingest(syslog("ie-sw2", "LINK-3-UPDOWN", interface="Gi1/0/1", state="down"))
        await q.ingest(syslog("ie-sw2", "LINK-3-UPDOWN", interface="Gi1/0/5", state="down"))
        await q.tick()
        assert sorted(i.key for i in await q.incidents()) == [
            "fault:device:ie-sw2", "fault:rep-segment:1"]
    run(go())


def test_duplicates_are_suppressed_and_storms_escalate_once():
    async def go():
        q, board, clock, _ = make_queen(storm_threshold=5)
        for _ in range(12):
            await q.ingest(syslog("ie-sw4", "LINK-3-UPDOWN", interface="Gi1/0/5", state="down"))
            clock.advance(0.1)
        await q.tick()
        (inc,) = await q.incidents()
        assert inc.storm and inc.priority == 0
        assert inc.signal_count == 12 and inc.suppressed == 11
        directives = await q.directives()
        assert len(directives) == 1 and directives[0].priority == 0      # one task, not twelve
    run(go())


def test_down_and_up_are_different_facts():
    async def go():
        q, *_ = make_queen()
        await q.ingest(syslog("sw", "LINK-3-UPDOWN", interface="Gi1", state="down"))
        await q.ingest(syslog("sw", "LINK-3-UPDOWN", interface="Gi1", state="up"))
        await q.tick()
        (inc,) = await q.incidents()
        assert inc.suppressed == 0
    run(go())


def test_human_requests_are_never_deduplicated():
    async def go():
        q, board, clock, auth = make_queen()
        for _ in range(2):
            await q.request(auth.issue("manish"), "why is ptp unlocked", device="ie-sw5")
        await q.tick()
        assert len([i for i in await q.incidents() if i.lane == "request"]) == 2
    run(go())


def test_quiet_incidents_close():
    async def go():
        q, board, clock, _ = make_queen(correlation_window_s=10)
        await q.ingest(syslog("sw", "SYS-5-CONFIG_I"))
        await q.tick()
        for d in await q.directives():                  # nobody to do the work: cancel it
            await q.dispatch.cancel(d.id, "test")
        clock.advance(11)
        await q.tick()
        assert all(i.closed for i in await q.incidents())
    run(go())
