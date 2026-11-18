import pytest

from netskills.fake import FakeGateway, plant_network
from netskills.gateway import PolicyGateway

from .helpers import run

INTENT = {f"ie-sw{i}": {"config": {"hostname": f"ie-sw{i}"}} for i in range(1, 5)}


def gw():
    return PolicyGateway(FakeGateway(plant_network(INTENT)), writers={"deployer"})


def test_allowlist_is_exact():
    async def go():
        p = gw()
        reader = p.for_drone("tshoot")
        assert await reader.run_show("ie-sw1", "show rep topology")
        for bad in ("show run | inc pass", "show tech-support", "show version\nreload",
                    "conf t"):
            with pytest.raises(PermissionError):
                await reader.run_show("ie-sw1", bad)
    run(go())


def test_only_writers_write_and_only_with_a_rollback_timer():
    async def go():
        p = gw()
        with pytest.raises(PermissionError, match="may not write"):
            await p.for_drone("tshoot").apply("ie-sw1", ["hostname x"], 5)
        with pytest.raises(PermissionError, match="rollback timer"):
            await p.for_drone("deployer").apply("ie-sw1", ["hostname x"], 0)
        cp = await p.for_drone("deployer").apply("ie-sw1", ["hostname x"], 5)
        await p.for_drone("deployer").rollback("ie-sw1", cp)
        assert (await p.for_drone("tshoot").get_config("ie-sw1"))["hostname"] == "ie-sw1"
        assert sum(not c.allowed for c in p.log) == 2
    run(go())
