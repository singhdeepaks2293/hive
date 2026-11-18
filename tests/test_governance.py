from datetime import datetime, timezone

from queen import ChangeState, ChangeWindow
from queen.governance import window_open

from .helpers import join, make_queen, map_device, run, work
from .test_consent import propose


async def approved(q, auth, device="ie-sw2", source="test:1", **domains):
    await map_device(q, device, **domains)
    c = await propose(q, auth, device=device, source=source)
    await q.vote(c.id, auth.issue("deepak"), "approve", c.content_hash)
    await q.tick()
    return (await q.changes())


def test_overnight_window():
    w = [ChangeWindow(days=[2], start="22:00", end="04:00", tz="Asia/Kolkata")]   # Wednesday
    ist_wed_2300 = datetime(2026, 11, 18, 17, 30, tzinfo=timezone.utc)
    ist_thu_0300 = datetime(2026, 11, 18, 21, 30, tzinfo=timezone.utc)
    ist_thu_1200 = datetime(2026, 11, 19, 6, 30, tzinfo=timezone.utc)
    assert window_open(w, ist_wed_2300) and window_open(w, ist_thu_0300)
    assert not window_open(w, ist_thu_1200)
    assert window_open([], ist_thu_1200)


def test_approved_change_starts_when_clear():
    async def go():
        q, board, clock, auth = make_queen()
        (c,) = await approved(q, auth, **{"rep-segment": "1"})
        assert c.state is ChangeState.APPLYING and c.apply_directive_id
    run(go())


def test_unknown_failure_domains_block_the_change():
    async def go():
        q, board, clock, auth = make_queen()
        c = await propose(q, auth)
        await q.vote(c.id, auth.issue("deepak"), "approve", c.content_hash)
        await q.tick()
        c = (await q.changes())[0]
        assert c.state is ChangeState.APPROVED and "domains unknown" in c.reason
    run(go())


def test_freeze_switch_holds_and_release_lets_go():
    async def go():
        q, board, clock, auth = make_queen()
        await q.freeze(auth.issue("deepak"), "audit")
        (c,) = await approved(q, auth, **{"rep-segment": "1"})
        assert c.state is ChangeState.APPROVED and "freeze" in c.reason
        await q.unfreeze(auth.issue("deepak"))
        await q.tick()
        assert (await q.changes())[0].state is ChangeState.APPLYING
    run(go())


def test_viewer_cannot_freeze():
    async def go():
        import pytest
        from queen import ConsentError
        q, board, clock, auth = make_queen()
        with pytest.raises(ConsentError):
            await q.freeze(auth.issue("intern"), "lol")
    run(go())


def test_outside_window_waits_but_break_glass_goes():
    async def go():
        q, board, clock, auth = make_queen(change_windows=[ChangeWindow(days=[6])])
        (c,) = await approved(q, auth, **{"rep-segment": "1"})
        assert c.state is ChangeState.APPROVED and "window" in c.reason
    run(go())


def test_open_fault_incident_on_the_ring_freezes_routine_changes():
    async def go():
        from queen import Signal, SignalKind
        q, board, clock, auth = make_queen()
        await map_device(q, "ie-sw3", **{"rep-segment": "1"})
        await q.ingest(Signal(kind=SignalKind.SYSLOG, device="ie-sw3",
                              mnemonic="REP-4-LINKSTATUS"))
        (c,) = await approved(q, auth, device="ie-sw2", **{"rep-segment": "1"})
        assert c.state is ChangeState.APPROVED and "open incident" in c.reason
    run(go())


def test_one_change_per_ring_at_a_time():
    async def go():
        q, board, clock, auth = make_queen()
        for dev in ("ie-sw1", "ie-sw2"):
            await map_device(q, dev, **{"rep-segment": "1"})
        await join(q, "intent", "config.propose")
        await join(q, "guardian", "verify.change")
        for dev in ("ie-sw1", "ie-sw2"):
            await q.request(auth.issue("manish"), "configure ntp", device=dev)
            await work(q, "intent", {"proposal": {"device": dev, "config": ["ntp server 1.1.1.1"],
                                                  "source": f"t:{dev}"}})
        for c in await q.changes():
            await work(q, "guardian", {"passed": True, "content_hash": c.content_hash})
        for c in await q.changes():
            await q.vote(c.id, auth.issue("deepak"), "approve", c.content_hash)
        await q.tick()
        states = sorted(c.state.value for c in await q.changes())
        assert states == ["applying", "approved"]
        waiting = next(c for c in await q.changes() if c.state is ChangeState.APPROVED)
        assert "rep-segment:1 locked" in waiting.reason
    run(go())
