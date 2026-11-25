"""Lists the creator picks from (API design group D): D3 voices and D4 music."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import music, voices
from ..auth import require_user
from ..errors import ApiError
from ..script_rules import MOODS

bp = Blueprint("catalogue", __name__)


@bp.get("/voices")
@require_user()
def list_voices():
    """D3. The curated voices, with a sample clip for each when ElevenLabs is reachable."""
    return jsonify(items=voices.catalogue_json())


@bp.get("/music")
@require_user()
def list_music():
    """D4. The music library, optionally only tracks tagged with one mood."""
    mood = request.args.get("mood") or None
    if mood is not None and mood not in MOODS:
        raise ApiError(
            400,
            "validation_error",
            "Some details need another look.",
            {"fields": {"mood": "Choose one of the moods"}},
        )
    return jsonify(items=[music.track_json(t) for t in music.list_tracks(mood)])
