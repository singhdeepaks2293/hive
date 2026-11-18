import pytest

from queen import InMemoryBlackboard, NotLeader, QueenLink, Signal, SignalKind

from .helpers import FakeClock, join, make_queen, run


def test_one_leader_standby_takes_over_after_lease_expiry():
    async def go():
        board, clock = InMemoryBlackboard(), FakeClock()
        a, *_ = make_queen(board, clock, name="a", leader_ttl_s=5)
        b, *_ = make_queen(board, clock, name="b", leader_ttl_s=5)
        assert await a.tick() is True and await b.tick() is False
        with pytest.raises(NotLeader):
            await join(b, "d", "tshoot")
        await a.stop(release=False)                          # crash
        assert await a.tick() is False                        # a crashed Queen does nothing
        with pytest.raises(NotLeader):
            await join(a, "d", "tshoot")
        assert await b.tick() is False                        # lease still held by a
        clock.advance(6)
        assert await b.tick() is True
    run(go())


def test_new_queen_continues_from_the_blackboard():
    async def go():
        board, clock = InMemoryBlackboard(), FakeClock()
        a, *_ = make_queen(board, clock, name="a", leader_ttl_s=5)
        b, *_ = make_queen(board, clock, name="b", leader_ttl_s=5)
        link = QueenLink(a, b)
        await a.tick()
        await join(a, "expert", "tshoot.ptp")
        await link.ingest(Signal(kind=SignalKind.SYSLOG, device="s", mnemonic="PTP-4-X"))
        await a.tick()
        await a.stop(release=False)
        clock.advance(6)
        await b.tick()
        d = await link.receive("expert")                     # same work, new Queen
        assert d is not None and d.skill == "tshoot.ptp"
        await link.complete(d.id, "expert", {})
    run(go())


def test_events_cannot_claim_identity_or_impersonate_drift():
    async def go():
        q, *_ = make_queen()
        sig = await q.ingest(Signal(kind=SignalKind.SYSLOG, device="s", principal="manish"))
        assert sig.principal is None
        with pytest.raises(ValueError):
            await q.ingest(Signal(kind=SignalKind.DRIFT, device="s"))
        with pytest.raises(ValueError):
            await q.ingest(Signal(kind=SignalKind.REQUEST, text="configure everything"))
    run(go())
