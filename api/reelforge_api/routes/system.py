"""System endpoints (API design group J)."""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from ..extensions import db
from ..storage import get_storage

bp = Blueprint("system", __name__)
log = logging.getLogger(__name__)


def _check(name: str, probe) -> str:
    try:
        probe()
        return "ok"
    except Exception:  # noqa: BLE001 - a health check reports any failure rather than raising
        log.warning("health check failed", extra={"dependency": name}, exc_info=True)
        return "down"


@bp.get("/health")
def health():
    """J1. Used by the load balancer: 200 when every dependency answers, 503 otherwise."""
    checks = {
        "db": _check("db", lambda: db.session.execute(text("SELECT 1"))),
        "redis": _check("redis", lambda: current_app.extensions["redis"].ping()),
        "storage": _check("storage", lambda: get_storage().check()),
    }
    healthy = all(state == "ok" for state in checks.values())
    body = {
        "status": "ok" if healthy else "degraded",
        "version": current_app.extensions["settings"].app_version,
    }
    return jsonify(**body, **checks), 200 if healthy else 503
