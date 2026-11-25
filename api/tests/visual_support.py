"""Shared by the visual tests (milestone B4 slice 2): helpers for C8, C9 and C10.

The network guard is the one in voice_support (autouse there; imported by each test module):
real provider hosts fail the test, any other address answers as if offline and is recorded.
"""

from __future__ import annotations

import io
import uuid

from PIL import Image

from .projects_support import get_project, scripted_project

VISUAL_FIELDS = {
    "assetId", "url", "durationSeconds", "kind", "thumbnailUrl", "provider", "author",
    "sourceUrl", "width", "height", "stockFallback",
}  # fmt: skip
STOCK_RESULT_FIELDS = {
    "providerId", "thumbnailUrl", "previewUrl", "durationSeconds", "width", "height", "author",
    "source", "pageUrl",
}  # fmt: skip


def regenerate_visual(browser, scene_id: str, body: dict | None = None, *, key: str | None = None):
    headers = {"Idempotency-Key": key} if key is not None else None
    return browser.post(f"/scenes/{scene_id}/visual/regenerate", body, headers=headers)


def visual_started(browser, scene_id: str, body: dict | None = None) -> dict:
    res = regenerate_visual(browser, scene_id, body)
    assert res.status_code == 202, res.get_data(as_text=True)
    return res.get_json()["job"]


def select_visual(browser, scene_id: str, body: dict):
    return browser.post(f"/scenes/{scene_id}/visual/select", body)


def stock_search(browser, scene_id: str, **query):
    params = "&".join(f"{k}={v}" for k, v in query.items())
    return browser.get(f"/scenes/{scene_id}/stock-search" + (f"?{params}" if params else ""))


def scene_of(browser, project_id: str, scene_id: str) -> dict:
    return next(s for s in get_project(browser, project_id)["scenes"] if s["id"] == scene_id)


def visual_project(app, me, **scene_fields) -> tuple[dict, str]:
    """A scripted faceless project and its first scene, with these scene fields set (C2)."""
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    if scene_fields:
        res = me.browser.patch(f"/scenes/{scene_id}", scene_fields)
        assert res.status_code == 200, res.get_data(as_text=True)
    return project, scene_id


def stored_visual_files(app, user_id: str, project_id: str) -> list[str]:
    storage = app.extensions["storage"]
    found = storage.client.list_objects_v2(
        Bucket=storage.bucket, Prefix=f"users/{user_id}/projects/{project_id}/visual/"
    )
    return sorted(item["Key"] for item in found.get("Contents", []))


def visual_assets(database, project_id: str) -> list[dict]:
    return database.rows(
        "SELECT id::text, kind, s3_key, content_type, size_bytes, duration_seconds, width, height, "
        "provider, meta, replaced_at FROM assets WHERE project_id = :p AND kind IN ('video_clip', 'image') "
        "ORDER BY created_at",
        p=project_id,
    )


def upload_asset(database, user_id: str, project_id: str | None, *, content_type="video/mp4",
                 kind="upload", status="ready", replaced=False) -> str:  # fmt: skip
    """A finished upload as D1/D2 (milestone B7) will leave it."""
    asset_id = str(uuid.uuid4())
    database.run(
        "INSERT INTO assets (id, user_id, project_id, kind, status, s3_key, content_type, provider, "
        "width, height, duration_seconds, replaced_at) VALUES (:id, :u, :p, :k, :st, :key, :ct, 'upload', "
        "1080, 1920, 6.5, CASE WHEN :r THEN now() END)",
        id=asset_id, u=user_id, p=project_id, k=kind, st=status, key=f"test/upload/{asset_id}",
        ct=content_type, r=replaced,
    )  # fmt: skip
    return asset_id


def jpeg_bytes(width: int = 64, height: int = 96) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 100, 50)).save(buf, "JPEG")
    return buf.getvalue()


def png_bytes(width: int = 64, height: int = 96) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (20, 100, 50)).save(buf, "PNG")
    return buf.getvalue()


def gif_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "GIF")
    return buf.getvalue()


MP4_HEAD = b"\x00\x00\x00\x18ftypmp42"
