from queen import DirectiveState, Signal, SignalKind

from .helpers import join, make_queen, run, work


def rep_event(device="ie-sw1"):
    return Signal(kind=SignalKind.SYSLOG, device=device, mnemonic="PTP-4-HOLDOVER", severity=4)


def test_most_specific_skill_wins():
    async def go():
        q, *_ = make_queen()
        await join(q, "generalist", "tshoot")
        await join(q, "expert", "tshoot.ptp")
        await q.ingest(rep_event())
        assert (await work(q, "generalist")) is None
        assert (await work(q, "expert")).skill == "tshoot.ptp"
    run(go())


def test_generalist_covers_when_no_expert():
    async def go():
        q, *_ = make_queen()
        await join(q, "generalist", "tshoot")
        await q.ingest(rep_event())
        assert (await work(q, "generalist")).skill == "tshoot.ptp"
    run(go())


def test_unroutable_waits_then_routes_when_a_drone_joins():
    async def go():
        q, *_ = make_queen()
        await q.ingest(rep_event())
        await q.tick()
        (d,) = await q.directives()
        assert d.state is DirectiveState.READY and d.unroutable_since is not None
        await join(q, "late", "tshoot.ptp")
        assert (await work(q, "late")) is not None
    run(go())


def test_retry_prefers_a_drone_that_has_not_tried():
    async def go():
        q, board, clock, _ = make_queen(backoff_base_s=1)
        await join(q, "a", "tshoot.ptp")
        await join(q, "b", "tshoot.ptp")
        await q.ingest(rep_event())
        await q.tick()
        first = await q.receive("a") or await q.receive("b")
        await q.fail(first.id, first.assigned_to, "timeout talking to device")
        clock.advance(2)
        await q.tick()
        other = "b" if first.assigned_to == "a" else "a"
        assert (await q.receive(other)) is not None
    run(go())


def test_failing_drone_is_severed_then_regenerates():
    async def go():
        q, board, clock, _ = make_queen(sever_after=2, sever_s=60, backoff_base_s=0.1,
                                        max_attempts=10)
        await join(q, "flaky", "tshoot.ptp")
        await q.ingest(rep_event())
        for _ in range(2):
            await q.tick()
            d = await q.receive("flaky")
            await q.fail(d.id, "flaky", "boom")
            clock.advance(1)
        drone = await q.registry.get("flaky")
        assert drone.status.value == "severed"
        await q.tick()
        assert await q.receive("flaky") is None
        clock.advance(61)
        await q.heartbeat("flaky")
        await q.tick()
        assert (await q.registry.get("flaky")).status.value == "active"
    run(go())


def test_drones_cannot_propose_changes_unless_they_are_proposers():
    async def go():
        q, *_ = make_queen()
        await join(q, "expert", "tshoot.ptp")
        await q.ingest(rep_event())
        await work(q, "expert", {"proposal": {"device": "ie-sw1", "config": "reload",
                                              "source": "me"}})
        assert await q.changes() == []
    run(go())


def test_lost_lease_fences_the_drone_off():
    async def go():
        q, board, clock, _ = make_queen(lease_ttl_s=5)
        await join(q, "slow", "tshoot.ptp")
        await q.ingest(rep_event())
        await q.tick()
        d = await q.receive("slow")
        clock.advance(6)
        assert await q.renew(d.id, "slow") is False
        try:
            await q.complete(d.id, "slow", {})
            raise AssertionError("expired lease must be refused")
        except PermissionError:
            pass
    run(go())
