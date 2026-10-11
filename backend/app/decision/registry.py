"""Choosing a decision engine, and saying honestly which one answered.

The swap point was always the ``DecisionEngine`` protocol; this is the lookup
that makes it usable from configuration and from a request, so adding a fourth
engine means writing one class and one line here.

Engines are cached per process because two of them hold model weights, and
re-instantiating those per request would dominate the latency the whole design
is built around.

The ``local_only`` guard is the part worth keeping. An engine that sends a
caller's words off the machine cannot be selected by a request parameter
alone -- it has to be enabled in configuration first. A demo that can be
pointed at a third party by a query string is one typo away from making the
project's main claim false without anyone noticing.
"""

from __future__ import annotations

import logging

from ..config import settings
from .base import DecisionEngine

log = logging.getLogger("dispatchiq.registry")

_ENGINES: dict[str, DecisionEngine] = {}


def _build(name: str) -> DecisionEngine | None:
    if name == "laya":
        from .laya_model import LayaEngine

        return LayaEngine()
    if name == "rules":
        from .rules import RuleEngine

        return RuleEngine()
    if name == "openai":
        from .openai_model import OpenAIEngine

        return OpenAIEngine()
    if name == "jev":
        from .jev_model import JevEngine

        return JevEngine()
    return None


def available() -> list[str]:
    """Engines this deployment will allow, in preference order."""
    names = ["laya", "rules"]
    if settings.allow_cloud_inference:
        names.extend(["openai", "jev"])
    elif settings.jev_is_local:
        # A self-hosted Jev endpoint is not cloud inference, so it does not
        # need the cloud switch.
        names.append("jev")
    return names


def get_engine(name: str | None = None) -> DecisionEngine:
    """Return an engine by name, falling back to the configured default.

    A request asking for an engine this deployment has not enabled gets the
    default rather than an error: refusing the call would be worse during a
    demo, and silently using the cloud would be worse still, so it does the
    local thing and says so in the notes.
    """
    requested = (name or settings.decision_engine or "laya").lower()

    if requested in ("openai", "jev") and requested not in available():
        log.warning(
            "%s requested but not enabled for this deployment "
            "(set DISPATCHIQ_ALLOW_CLOUD=1); using a local engine instead",
            requested,
        )
        requested = "laya"

    if requested not in _ENGINES:
        engine = _build(requested)
        if engine is None:
            log.warning("unknown decision engine %r; using laya", requested)
            requested = "laya"
            engine = _build("laya")
        _ENGINES[requested] = engine  # type: ignore[assignment]

    return _ENGINES[requested]


def describe_all() -> list[dict]:
    """What each available engine is and where it runs. For /api/health."""
    described: list[dict] = []
    for name in available():
        engine = _ENGINES.get(name) or _build(name)
        if engine is None:
            continue
        try:
            described.append({**engine.describe(), "ready": engine.ready()})
        except Exception as exc:  # pragma: no cover - a broken engine still lists
            described.append({"name": name, "error": str(exc), "ready": False})
    return described


def inference_is_local(engine: DecisionEngine) -> bool:
    return bool(getattr(engine, "is_local", True))
