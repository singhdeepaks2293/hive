"""scripts/run_queen_demo.py -- end-to-end Queen demo with fake workers.

    python scripts/run_queen_demo.py     (from repo root; needs pydantic)

Shows: syslog -> task DAG -> workers, and a change request gated by
guardian + human quorum. No devices touched; swap InMemoryBlackboard for
blackboard/store.py (Postgres) and fake workers for workers/*.py later.
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

from queen import Event, EventKind, Queen
from queen.blackboard import InMemoryBlackboard


async def fake_worker(q: Queen, name: str) -> None:
    while True:
        task = await q.claim(name)
        if task:
            await asyncio.sleep(0.05)                      # pretend to do work
            await q.complete(task.id, name, {"ok": True, "by": name})
        else:
            await asyncio.sleep(0.01)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    q = Queen(InMemoryBlackboard())
    await q.start()
    workers = [asyncio.create_task(fake_worker(q, w))
               for w in ("parser", "analyst", "scout", "guardian")]

    # 1) device event: interface flap on an Industrial Ethernet switch
    await q.ingest(Event(kind=EventKind.SYSLOG, source="ie-sw1", device="ie-sw1",
                         severity=3, mnemonic="LINK-3-UPDOWN",
                         text="Interface Gi1/0/1, changed state to down",
                         payload={"interface": "Gi1/0/1"}))

    # 2) human change request: must pass guardian + human quorum
    await q.request("deepak", "configure rep segment 1 on ie-sw2", device="ie-sw2")
    await asyncio.sleep(0.3)
    change = next(t for t in await q.snapshot() if t.spec.type == "verify_change")
    print(f"\n>> change task {change.id} is {change.state.value}; approving...\n")
    await q.approve(change.id, "guardian", note="diff verified against runbook")
    await q.approve(change.id, "human:deepak", note="approved in maintenance window")
    await asyncio.sleep(0.3)

    print("\n== final task board ==")
    for t in sorted(await q.snapshot(), key=lambda t: t.created_at):
        print(f"{t.id}  {t.spec.type:<14} {t.spec.owner:<10} "
              f"{t.spec.device or '-':<8} {t.state.value}")
    print("\nqueen metrics:", q.metrics)
    for w in workers:
        w.cancel()
    await q.stop()


if __name__ == "__main__":
    asyncio.run(main())