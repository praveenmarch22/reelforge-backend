"""Lists the creator picks from (API design group D): D3 voices. D4 music comes in slice 3."""

from __future__ import annotations

from flask import Blueprint, jsonify

from .. import voices
from ..auth import require_user

bp = Blueprint("catalogue", __name__)


@bp.get("/voices")
@require_user()
def list_voices():
    """D3. The curated voices, with a sample clip for each when ElevenLabs is reachable."""
    return jsonify(items=voices.catalogue_json())
