"""File storage against the local MinIO, using the same calls that will run against S3."""

import urllib.error
import urllib.request
import uuid

import pytest


@pytest.fixture
def storage(app):
    return app.extensions["storage"]


@pytest.fixture
def key(storage):
    key = f"tests/{uuid.uuid4().hex}.txt"
    yield key
    storage.delete(key)


def test_ensure_bucket_is_safe_to_repeat(storage):
    assert storage.ensure_bucket() is False  # conftest already created it


def test_put_exists_delete(storage, key):
    assert storage.exists(key) is False
    storage.put_bytes(key, b"hello", "text/plain")
    assert storage.exists(key) is True
    storage.delete(key)
    assert storage.exists(key) is False


def test_presigned_get_downloads_the_file_without_credentials(storage, key):
    storage.put_bytes(key, b"reel bytes", "text/plain")
    with urllib.request.urlopen(storage.presigned_get(key), timeout=5) as res:  # noqa: S310
        assert res.read() == b"reel bytes"


def test_presigned_put_uploads_with_the_declared_content_type(storage, key):
    url = storage.presigned_put(key, "text/plain")
    req = urllib.request.Request(url, data=b"uploaded", method="PUT", headers={"Content-Type": "text/plain"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=5) as res:  # noqa: S310
        assert res.status == 200
    assert storage.exists(key)


def test_presigned_put_refuses_a_different_content_type(storage, key):
    url = storage.presigned_put(key, "text/plain")
    req = urllib.request.Request(url, data=b"x", method="PUT", headers={"Content-Type": "video/mp4"})  # noqa: S310
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)  # noqa: S310
    assert err.value.code == 403
    assert not storage.exists(key)


def test_files_are_private_without_a_signed_link(storage, key):
    storage.put_bytes(key, b"private", "text/plain")
    plain_url = storage.presigned_get(key).split("?")[0]
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(plain_url, timeout=5)  # noqa: S310
    assert err.value.code == 403
