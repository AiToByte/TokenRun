"""
S3 Gateway — read data from S3-compatible object storage.

Requires ``boto3`` to be installed.  Supports any S3-compatible endpoint
(AWS S3, MinIO, Cloudflare R2, etc.).
"""

from __future__ import annotations

from typing import Any, Dict, Generator, List, Optional

__all__ = ["S3Gateway"]


class S3Gateway:
    """Stream objects from an S3 bucket.

    Parameters
    ----------
    bucket:
        S3 bucket name.
    prefix:
        Object key prefix to filter by.
    endpoint_url:
        Optional custom endpoint (e.g. ``http://localhost:9000`` for MinIO).
    region_name:
        AWS region name.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        region_name: str = "us-east-1",
    ) -> None:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "S3Gateway requires boto3. Install with: pip install boto3"
            )
        self.bucket = bucket
        self.prefix = prefix
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
        )

    def stream_objects(
        self, max_keys: int = 1000
    ) -> Generator[Dict[str, Any], None, None]:
        """Yield object metadata and content for keys matching the prefix."""
        paginator = self._client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=self.bucket,
            Prefix=self.prefix,
            PaginationConfig={"MaxItems": max_keys},
        )
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                try:
                    resp = self._client.get_object(Bucket=self.bucket, Key=key)
                    content = resp["Body"].read().decode("utf-8")
                    yield {
                        "key": key,
                        "size": obj["Size"],
                        "content": content,
                    }
                except (UnicodeDecodeError, Exception) as exc:
                    yield {
                        "key": key,
                        "size": obj["Size"],
                        "content": None,
                        "error": str(exc),
                    }

    def read_object(self, key: str) -> str:
        """Read a single object by key."""
        resp = self._client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read().decode("utf-8")
