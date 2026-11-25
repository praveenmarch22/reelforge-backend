"""File storage on S3 (Amazon S3 in AWS, MinIO locally). The same code talks to both.

Files never pass through the API: browsers upload and download with short-lived presigned links.
"""

from __future__ import annotations

from functools import cached_property

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError
from flask import current_app

from .config import Settings


class Storage:
    def __init__(self, settings: Settings):
        self._s = settings
        self.bucket = settings.s3_bucket

    def _client(self, endpoint_url: str) -> BaseClient:
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=self._s.s3_access_key,
            aws_secret_access_key=self._s.s3_secret_key,
            region_name=self._s.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=3,
                read_timeout=10,
                retries={"total_max_attempts": 2},
            ),
        )

    @cached_property
    def client(self) -> BaseClient:
        """For the API's and workers' own calls."""
        return self._client(self._s.s3_endpoint_url)

    @cached_property
    def presign_client(self) -> BaseClient:
        """For links handed to browsers: a signature is tied to the address it was made for."""
        return self._client(self._s.s3_presign_endpoint_url)

    def ensure_bucket(self) -> bool:
        """Creates the bucket if it is missing. Returns True if it was created."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return False
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                raise
        self.client.create_bucket(Bucket=self.bucket)
        return True

    def check(self) -> None:
        """Raises if the bucket can't be reached."""
        self.client.head_bucket(Bucket=self.bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def put_fileobj(self, key: str, fileobj, content_type: str) -> None:
        """Streams a file to storage in parts, so a large clip is never held in memory."""
        self.client.upload_fileobj(fileobj, self.bucket, key, ExtraArgs={"ContentType": content_type})

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presigned_get(self, key: str, expires_seconds: int = 7200) -> str:
        return self.presign_client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_seconds
        )

    def presigned_put(self, key: str, content_type: str, expires_seconds: int = 900) -> str:
        """A link the browser can PUT one file to. The Content-Type must match when uploading."""
        return self.presign_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_seconds,
        )


def get_storage() -> Storage:
    return current_app.extensions["storage"]
