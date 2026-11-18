"""drones -- the collective's workers, one module per unimatrix."""
from .base import Drone, DroneFailure, handles
from .builder import BuilderDrone
from .deployer import DeployerDrone
from .guardian import GuardianDrone
from .intent import IntentDrone
from .intent_repo import GitHubIntentRepo, LocalIntentRepo, PullRequest
from .mapper import MapDrone
from .sense import SenseDrone
from .sentinel import SentinelDrone
from .tshoot import TshootDrone

__all__ = ["Drone", "DroneFailure", "handles", "BuilderDrone", "DeployerDrone",
           "GuardianDrone", "IntentDrone", "LocalIntentRepo", "GitHubIntentRepo",
           "PullRequest", "MapDrone", "SenseDrone", "SentinelDrone", "TshootDrone"]
