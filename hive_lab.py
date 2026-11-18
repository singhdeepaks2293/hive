"""hive_lab.py -- assemble a complete collective against the simulated plant.

    lab = build_lab()            # two Queens, eight drones, ring 1, intent repo
    await lab.start(); ...; await lab.stop()

Used by scripts/demo.py and tests/test_end_to_end.py. Production wiring is
the same shape with NetmikoGateway, GitHubIntentRepo and a Postgres board.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from drones import (BuilderDrone, DeployerDrone, Drone, GuardianDrone, IntentDrone,
                    LocalIntentRepo, MapDrone, SenseDrone, SentinelDrone, TshootDrone)
from netskills.fake import FakeGateway, FakeNetwork, plant_network
from netskills.gateway import PolicyGateway
from queen import (ConsentPolicy, HmacAuthenticator, InMemoryBlackboard, Queen, QueenConfig,
                   QueenLink)

INTENT_DIR = Path(__file__).parent / "intent" / "devices"

USERS = {
    "manish": {"netops-approver"},
    "deepak": {"netops-approver"},
    "intern": {"netops-viewer"},
}

# Borg designations: "<n> of <total>, <role>"
DRONE_PLAN = [
    ("sense", "One of Nine, Sense"),
    ("map", "Two of Nine, Map"),
    ("sentinel", "Three of Nine, Sentinel"),
    ("tshoot", "Four of Nine, Tshoot"),
    ("tshoot_expert", "Five of Nine, Tshoot (REP/PTP)"),
    ("intent", "Six of Nine, Intent"),
    ("guardian", "Seven of Nine, Guardian"),
    ("deployer", "Eight of Nine, Deployer"),
    ("builder", "Nine of Nine, Builder"),
]


def lab_config(**overrides: float) -> QueenConfig:
    base = dict(tick_s=0.05, leader_ttl_s=1.0, dedup_window_s=2.0, storm_window_s=5.0,
                storm_threshold=8, correlation_window_s=2.0, lease_ttl_s=10.0,
                assign_timeout_s=3.0, drone_ttl_s=10.0, drift_interval_s=1.0,
                observed_ttl_s=30.0, drift_resignal_s=30.0, canary_soak_s=0.5,
                backoff_base_s=0.2)
    base.update(overrides)
    return QueenConfig(**base)


@dataclass
class Lab:
    board: InMemoryBlackboard
    auth: HmacAuthenticator
    queens: list[Queen]
    link: QueenLink
    network: FakeNetwork
    policy: PolicyGateway
    repo: LocalIntentRepo
    drones: dict[str, Drone]
    tokens: dict[str, str]
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: list[asyncio.Task] = field(default_factory=list)

    async def start(self) -> None:
        for q in self.queens:
            await q.start()
        await asyncio.sleep(0.1)                         # let one Queen ascend first
        for d in self.drones.values():
            self._tasks.append(asyncio.create_task(d.run(self._stop, idle_s=0.02,
                                                         patrol_every_s=0.5)))

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for q in self.queens:
            await q.stop()

    @property
    def leader(self) -> Optional[Queen]:
        return next((q for q in self.queens if q.leading), None)


def build_lab(queens: int = 2, config: Optional[QueenConfig] = None,
              deployer_soak_s: float = 0.3) -> Lab:
    board = InMemoryBlackboard()
    auth = HmacAuthenticator(b"lab-secret-not-for-production", USERS)
    workdir = Path(tempfile.mkdtemp(prefix="hive-intent-"))
    shutil.copytree(INTENT_DIR, workdir, dirs_exist_ok=True)
    repo = LocalIntentRepo(workdir)
    cfg = config or lab_config()
    names = ["queen-prime", "queen-secundus", "queen-tertius"][:queens]
    qs = [Queen(board, authenticator=auth, name=n, config=cfg,
                consent_policy=ConsentPolicy(), approvals=repo) for n in names]
    link = QueenLink(*qs)

    network = plant_network(repo.devices(), sink=link.ingest)
    designations = dict(DRONE_PLAN)
    policy = PolicyGateway(FakeGateway(network), writers={designations["deployer"]})

    def gw(role: str):
        return policy.for_drone(designations[role])

    devices = sorted(network.devices)
    drones: dict[str, Drone] = {
        "sense": SenseDrone(link, designations["sense"], gw("sense"), capacity=4),
        "map": MapDrone(link, designations["map"], gw("map"),
                        sites={d: network.devices[d].site for d in devices}, capacity=4),
        "sentinel": SentinelDrone(link, designations["sentinel"], gw("sentinel"),
                                  devices=devices),
        "tshoot": TshootDrone(link, designations["tshoot"], gw("tshoot"),
                              skills=["tshoot", "tshoot.interface", "tshoot.routing"],
                              capacity=2),
        "tshoot_expert": TshootDrone(link, designations["tshoot_expert"],
                                     gw("tshoot_expert"), skills=["tshoot.rep", "tshoot.ptp"],
                                     capacity=2),
        "intent": IntentDrone(link, designations["intent"], gw("intent"), repo=repo),
        "guardian": GuardianDrone(link, designations["guardian"], gw("guardian")),
        "deployer": DeployerDrone(link, designations["deployer"], gw("deployer"),
                                  soak_s=deployer_soak_s, revert_minutes=5),
        "builder": BuilderDrone(link, designations["builder"], gw("builder")),
    }
    tokens = {u: auth.issue(u) for u in USERS}
    return Lab(board, auth, qs, link, network, policy, repo, drones, tokens)
