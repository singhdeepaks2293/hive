"""drones/builder.py -- the Builder unimatrix: adapts the collective.

First cut drafts a TextFSM skeleton for a command whose output nobody can
parse yet. A human (or a later LLM-backed builder) fills the regexes, adds
recorded fixtures under tests/parser/, and the template ships once tests pass.
"""
from __future__ import annotations

from typing import Any

from queen.models import Directive

from .base import Drone, DroneFailure, handles


class BuilderDrone(Drone):
    unimatrix = "builder"

    @handles("template.build")
    async def build(self, d: Directive) -> dict[str, Any]:
        command = d.params.get("command")
        fields = d.params.get("fields") or []
        if not command or not fields:
            raise DroneFailure("template.build needs params.command and params.fields",
                               retryable=False)
        values = "\n".join(f"Value {f.upper()} (\\S+)" for f in fields)
        body = " +".join(f"${{{f.upper()}}}" for f in fields)
        template = f"{values}\n\nStart\n  ^\\s*{body} -> Record\n"
        return {"template": {"command": command, "text": template,
                             "status": "draft: needs fixtures and review"}}
