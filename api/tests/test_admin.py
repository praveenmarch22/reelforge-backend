"""Admin API (I1 to I13) and the clean-up of replaced files (milestone B8)."""

from __future__ import annotations

import uuid

from reelforge_api import cleanup
from reelforge_api.storage import get_storage

from .projects_support import creator, new_asset, scripted_project
from .support import assert_error


def make_admin(database, who) -> None:
    database.run("UPDATE users SET is_admin = true WHERE id = :id", id=who.id)


def test_only_admins_can_use_the_admin_api(app, database):
    me = creator(app)
    for method, path in (("get", "/admin/users"), ("get", "/admin/stats"), ("get", "/admin/providers")):
        assert_error(getattr(me.browser, method)(path), 403, "forbidden")


def test_users_search_detail_and_update(app, database):
    admin, other = creator(app), creator(app, name="Zed Person")
    make_admin(database, admin)
    found = admin.browser.get("/admin/users?q=zed").get_json()["items"]
    assert [u["id"] for u in found] == [other.id]
    assert {"balance", "projectCount", "isTester", "disabled"} <= set(found[0])
    detail = admin.browser.get(f"/admin/users/{other.id}").get_json()["user"]
    assert detail["recentJobs"] == [] and detail["youtube"] is None
    res = admin.browser.patch(f"/admin/users/{other.id}", {"isTester": True, "monthlyAllowance": 500})
    assert res.status_code == 200 and res.get_json()["user"]["isTester"] is True
    assert res.get_json()["user"]["monthlyAllowance"] == 500
    # Disabling signs the user out everywhere.
    assert admin.browser.patch(f"/admin/users/{other.id}", {"disabled": True}).status_code == 200
    assert other.browser.get("/me").status_code in (401, 403)
    # An admin can't lock themselves out.
    assert_error(admin.browser.patch(f"/admin/users/{admin.id}", {"disabled": True}), 409, "own_account")


def test_credit_adjustment_writes_the_ledger_and_never_goes_negative(app, database):
    admin, other = creator(app), creator(app)
    make_admin(database, admin)
    res = admin.browser.post(f"/admin/users/{other.id}/credits", {"amount": 25, "note": "Thanks for testing"})
    assert res.status_code == 201 and res.get_json()["balance"] == 125
    row = database.one(
        "SELECT amount, kind, note, created_by FROM credit_ledger WHERE user_id = :u AND kind = 'adjust'",
        u=other.id,
    )
    assert row["amount"] == 25 and row["note"] == "Thanks for testing" and str(row["created_by"]) == admin.id
    assert_error(
        admin.browser.post(f"/admin/users/{other.id}/credits", {"amount": -1000, "note": "x"}),
        409,
        "balance_negative",
    )
    assert (
        admin.browser.post(f"/admin/users/{other.id}/credits", {"amount": 5}).status_code == 400
    )  # a note is required


def test_provider_switch_needs_a_reason_and_shows_in_the_list(app, database):
    admin = creator(app)
    make_admin(database, admin)
    assert admin.browser.patch("/admin/providers/elevenlabs", {"enabled": False}).status_code == 400
    res = admin.browser.patch("/admin/providers/elevenlabs", {"enabled": False, "reason": "Quota used up"})
    assert res.status_code == 200 and res.get_json()["provider"]["enabled"] is False
    names = {p["name"]: p for p in admin.browser.get("/admin/providers").get_json()["items"]}
    assert names["elevenlabs"]["reason"] == "Quota used up" and names["gemini"]["enabled"] is True
    assert admin.browser.patch("/admin/providers/elevenlabs", {"enabled": True}).status_code == 200
    assert admin.browser.patch("/admin/providers/nope", {"enabled": True}).status_code == 404


def test_jobs_list_and_retry_rules(app, database):
    admin = creator(app)
    make_admin(database, admin)
    scripted_project(app, admin.browser)
    items = admin.browser.get("/admin/jobs?type=script").get_json()["items"]
    assert items and items[0]["type"] == "script" and items[0]["userEmail"]
    assert_error(admin.browser.post(f"/admin/jobs/{items[0]['id']}/retry"), 409, "not_failed")
    assert admin.browser.post(f"/admin/jobs/{uuid.uuid4()}/retry").status_code == 404


def test_music_add_update_and_delete(app, database):
    admin = creator(app)
    make_admin(database, admin)
    body = {
        "title": "Test Track", "moods": ["calm"], "license": "CC BY 4.0: test", "filename": "t.mp3",
        "contentType": "audio/mpeg", "sizeBytes": 1000, "durationSeconds": 60,
    }  # fmt: skip
    res = admin.browser.post("/admin/music", body)
    assert res.status_code == 201, res.get_data(as_text=True)
    track = res.get_json()["track"]
    assert track["isActive"] is False and res.get_json()["uploadUrl"]
    # Can't switch it on before its file exists.
    assert_error(admin.browser.patch(f"/admin/music/{track['id']}", {"isActive": True}), 409, "file_missing")
    with app.app_context():
        key = database.value("SELECT s3_key FROM music_tracks WHERE id = :id", id=track["id"])
        get_storage().put_bytes(key, b"ID3", "audio/mpeg")
    assert admin.browser.patch(f"/admin/music/{track['id']}", {"isActive": True}).get_json()["track"][
        "isActive"
    ]
    assert admin.browser.delete(f"/admin/music/{track['id']}").get_json()["deleted"] is True
    assert database.value("SELECT count(*) FROM music_tracks WHERE id = :id", id=track["id"]) == 0


def test_clean_up_deletes_old_replaced_files_nothing_uses(app, database):
    me = creator(app)
    project = scripted_project(app, me.browser)
    old = new_asset(database, me.id, project["id"])
    used = new_asset(database, me.id, project["id"])
    database.run(
        "UPDATE assets SET replaced_at = now() - interval '8 days' WHERE id IN (:a, :b)", a=old, b=used
    )
    database.run("UPDATE scenes SET voice_asset_id = :b WHERE project_id = :p", b=used, p=project["id"])
    with app.app_context():
        assert cleanup.clean_replaced() == 1
    assert database.value("SELECT count(*) FROM assets WHERE id = :a", a=old) == 0
    # The one a scene still uses stays, and no longer counts as replaced.
    assert database.value("SELECT replaced_at IS NULL FROM assets WHERE id = :b", b=used) is True
