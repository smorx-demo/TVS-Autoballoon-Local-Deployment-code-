"""
Outbound-data policy: the app runs fully local by default.

No drawing, text, image or file content may leave this machine unless an administrator sets
SMORX_ALLOW_EXTERNAL_AI=1. Every external AI call in the codebase goes through
`check_outbound` (via ai_fallback._http_json), so this flag is the single switch, and a call
attempted while it is off fails loudly instead of sending anything.

Hosts on SMORX_EGRESS_ALLOW (comma-separated, e.g. an on-premise model server) are always
allowed, because they keep data inside the company network.

Cloud file storage (AWS S3) is the other way data could leave: it is OFF unless an administrator
sets SMORX_ALLOW_CLOUD_STORAGE=1 AND names a bucket in AWS_S3_BUCKET. There is no default bucket.
The S3 client is only created through `check_cloud_storage`, which refuses while either is missing.
The two switches are separate on purpose: allowing external AI never uploads files, and vice versa.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

_ON = {"1", "true", "yes", "on"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ExternalCallBlocked(RuntimeError):
    """Raised when code tries to send data to a host outside the allowed set."""


def external_ai_allowed() -> bool:
    """True only when an administrator explicitly opted in to external AI services."""
    return os.environ.get("SMORX_ALLOW_EXTERNAL_AI", "").strip().lower() in _ON


def _allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("SMORX_EGRESS_ALLOW", "").split(",") if h.strip()}
    return _LOCAL_HOSTS | extra


def check_outbound(url: str) -> None:
    """Raise ExternalCallBlocked unless `url` may receive data under the current policy."""
    host = (urlparse(url).hostname or "").lower()
    if host in _allowed_hosts() or external_ai_allowed():
        return
    raise ExternalCallBlocked(
        f"Outbound call to {host or url!r} blocked: this installation runs local-only "
        "(set SMORX_ALLOW_EXTERNAL_AI=1 to allow external AI services)."
    )


def s3_bucket() -> str:
    """The bucket named by the administrator ('' when none: there is no built-in default)."""
    return os.environ.get("AWS_S3_BUCKET", "").strip()


def cloud_storage_allowed() -> bool:
    """True only when an administrator opted in to cloud storage and named the bucket."""
    return os.environ.get("SMORX_ALLOW_CLOUD_STORAGE", "").strip().lower() in _ON and bool(s3_bucket())


def check_cloud_storage() -> None:
    """Raise ExternalCallBlocked unless files may be sent to cloud storage."""
    if not cloud_storage_allowed():
        raise ExternalCallBlocked(
            "Cloud storage blocked: this installation keeps files on local disk "
            "(set SMORX_ALLOW_CLOUD_STORAGE=1 and AWS_S3_BUCKET to use S3)."
        )


def policy_summary() -> dict:
    return {
        "external_ai": external_ai_allowed(),
        "cloud_storage": cloud_storage_allowed(),
        "egress_allow": sorted(_allowed_hosts() - _LOCAL_HOSTS),
    }
