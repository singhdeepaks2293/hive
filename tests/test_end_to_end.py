"""The whole collective against the simulated plant, in real (short) time."""
import asyncio
import time

from hive_lab import build_lab
from queen import ChangeState

from .helpers import run


async def until(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await cond():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("condition not met")


async def fix_drift(lab):
    q = lab.link

    async def pending():
        return [c for c in await q.changes() if c.state is ChangeState.AWAITING_APPROVAL]
    await until(lambda: _nonempty(pending))
    (c,) = await pending()
    await q.vote(c.id, lab.tokens["deepak"], "approve", c.content_hash)

    async def applied():
        return (await q.changes())[0].state is ChangeState.APPLIED
    await until(applied)
    return c


async def _nonempty(fetch):
    return bool(await fetch())


def test_drift_is_found_proposed_verified_approved_and_applied():
    async def go():
        lab = build_lab()
        await lab.start()
        try:
            c = await fix_drift(lab)
            assert c.device == "ie-sw2" and "ntp server 10.0.0.1" in c.config
            cfg = lab.network.devices["ie-sw2"].config
            assert cfg["ntp server"] == "10.0.0.1"
            assert cfg["interface Gi1/0/1|description"] == "ring-1 to ie-sw3"
            writers = {call.caller for call in lab.policy.log
                       if call.action != "show" and call.allowed}
            assert writers == {"Eight of Nine, Deployer"}
        finally:
            await lab.stop()
    run(go())


def test_a_change_that_breaks_ptp_is_rolled_back_and_halts_the_rollout():
    async def go():
        lab = build_lab()
        await lab.start()
        try:
            await fix_drift(lab)
            for dev in lab.network.devices.values():
                dev.break_on_apply = "ptp"           # whatever goes first will break timing
            patch = {d: {"vlan 30|name": "cameras"} for d in lab.network.devices}
            await lab.link.request(lab.tokens["manish"], "add vlan 30", intent_patch=patch)
            await until(lambda: _true(bool(lab.repo.pull_requests())))
            pr = lab.repo.pull_requests()[0]
            lab.repo.approve(pr.id, "deepak")
            sha = lab.repo.merge(pr.id)

            async def failed_one():
                return any(c.state is ChangeState.FAILED for c in await lab.link.changes()
                           if c.rollout == lab.repo.source(sha))
            await until(failed_one, timeout=20)
            await asyncio.sleep(1.5)                     # give drift time to (not) re-propose

            mine = [c for c in await lab.link.changes() if c.rollout == lab.repo.source(sha)]
            failed = [c for c in mine if c.state is ChangeState.FAILED]
            assert len(failed) == 1 and "rolled back" in failed[0].reason
            others = [c for c in mine if c is not failed[0]]
            assert all(c.state is ChangeState.REJECTED and "rollout halted" in c.reason
                       for c in others)
            victim = lab.network.devices[failed[0].device]
            assert "vlan 30|name" not in victim.config and victim.ptp_state == "LOCKED"
            touched = {dev for dev, _ in lab.network.applied}
            assert touched == {"ie-sw2", failed[0].device}   # nobody else was touched
        finally:
            await lab.stop()
    run(go())


async def _true(v):
    return v
