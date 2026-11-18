"""queen/settings.py -- load QueenConfig and ConsentPolicy from YAML.

    cfg, policy = load_settings("config/hive.yaml")
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .consent import ConsentPolicy
from .models import QueenConfig


def load_settings(path: str | Path) -> tuple[QueenConfig, ConsentPolicy]:
    doc = yaml.safe_load(Path(path).read_text()) or {}
    return QueenConfig(**(doc.get("queen") or {})), ConsentPolicy(**(doc.get("consent") or {}))
