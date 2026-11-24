"""
Local-only installation: no drawing data leaves the machine.

Runs every fixture through the real upload path (convert → verify → balloon → Excel) with all
outbound network blocked by conftest, and asserts no outbound attempt was made at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.egress import ExternalCallBlocked, check_outbound, external_ai_allowed
from tests.conftest import OUTBOUND_ATTEMPTS

FIX = Path(__file__).parent / "fixtures"
SOURCES = sorted(p for p in FIX.iterdir() if p.suffix.lower() in {".pdf", ".dxf", ".step", ".stp"})


def test_external_ai_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SMORX_ALLOW_EXTERNAL_AI", raising=False)
    assert not external_ai_allowed()


@pytest.mark.parametrize("url", [
    "https://api.anthropic.com/v1/messages",
    "https://api.openai.com/v1/chat/completions",
    "https://example.com/anything",
])
def test_external_hosts_are_refused(monkeypatch, url):
    monkeypatch.delenv("SMORX_ALLOW_EXTERNAL_AI", raising=False)
    with pytest.raises(ExternalCallBlocked):
        check_outbound(url)


def test_local_and_allowlisted_hosts_pass(monkeypatch):
    monkeypatch.delenv("SMORX_ALLOW_EXTERNAL_AI", raising=False)
    monkeypatch.setenv("SMORX_EGRESS_ALLOW", "models.internal.example")
    check_outbound("http://127.0.0.1:11434/api/generate")
    check_outbound("http://models.internal.example/v1")


def test_guard_runs_before_any_request(monkeypatch):
    """The shared HTTP helper refuses before building a request, even with an API key present."""
    from app.services import ai_fallback

    monkeypatch.delenv("SMORX_ALLOW_EXTERNAL_AI", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")
    with pytest.raises(ExternalCallBlocked):
        ai_fallback._http_json("https://api.anthropic.com/v1/messages", {}, {"x": 1})


def test_ai_features_disabled_even_with_keys(monkeypatch):
    from app.services.anthropic_convert import anthropic_convert_enabled

    monkeypatch.delenv("SMORX_ALLOW_EXTERNAL_AI", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")
    monkeypatch.setenv("SMORX_AI_FALLBACK", "1")
    assert not anthropic_convert_enabled()


@pytest.mark.parametrize("src", SOURCES, ids=lambda p: p.name)
def test_full_pipeline_makes_no_outbound_call(src, tmp_path, monkeypatch):
    from app.services.autoballoon_engine import balloon_pdf
    from app.services.conversion_verify import run_conversion_verification
    from app.services.tvs_extraction_report import build_tvs_extraction_from_pdf
    from app.services.vector_pdf_convert import convert_to_vector_pdf, detect_format

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")   # a key alone must not enable anything
    monkeypatch.setenv("SMORX_AI_FALLBACK", "1")
    before = len(OUTBOUND_ATTEMPTS)
    dest = tmp_path / f"{src.stem}.pdf"
    kind = detect_format(src.name)
    meta = convert_to_vector_pdf(src, dest, kind, image_to_pdf=None)
    report = run_conversion_verification(kind, meta, dest)
    if kind != "pdf":
        assert report["method"] == "local"
        assert report["status"] in {"ok", "warning"}, report
    _, chars, _, _ = balloon_pdf(dest.read_bytes(), use_learning=False)
    assert chars
    build_tvs_extraction_from_pdf(dest, {})
    assert OUTBOUND_ATTEMPTS[before:] == []


# ----------------------------------------------------------------------------- cloud storage (S3)

@pytest.mark.parametrize("env", [
    {},                                                        # nothing set: local
    {"AWS_S3_BUCKET": "some-bucket"},                          # a bucket alone enables nothing
    {"SMORX_ALLOW_CLOUD_STORAGE": "1"},                        # opt-in without a bucket: no default bucket
    {"SMORX_ALLOW_CLOUD_STORAGE": "0", "AWS_S3_BUCKET": "some-bucket"},
])
def test_cloud_storage_off_unless_opted_in_with_a_bucket(monkeypatch, env):
    from app.core.egress import ExternalCallBlocked, check_cloud_storage, cloud_storage_allowed
    from app.utils import file_handler as fh

    for k in ("SMORX_ALLOW_CLOUD_STORAGE", "AWS_S3_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert not cloud_storage_allowed()
    assert not fh.s3_enabled() and fh.storage_mode() == "local"
    with pytest.raises(ExternalCallBlocked):
        check_cloud_storage()
    with pytest.raises(ExternalCallBlocked):
        fh._client()                                           # the S3 client is never built


def test_cloud_storage_needs_both_switches(monkeypatch):
    from app.core.egress import cloud_storage_allowed

    monkeypatch.setenv("SMORX_ALLOW_CLOUD_STORAGE", "1")
    monkeypatch.setenv("AWS_S3_BUCKET", "some-bucket")
    assert cloud_storage_allowed()
    monkeypatch.delenv("SMORX_ALLOW_EXTERNAL_AI", raising=False)
    from app.core.egress import external_ai_allowed
    assert not external_ai_allowed()                           # storage opt-in never enables AI


def test_local_storage_writes_to_disk(monkeypatch, tmp_path):
    from app.utils import file_handler as fh

    monkeypatch.delenv("SMORX_ALLOW_CLOUD_STORAGE", raising=False)
    monkeypatch.setattr(fh, "LOCAL_ROOT", tmp_path)
    fh.put_bytes("job1", "a.pdf", b"%PDF-1.4")
    assert (tmp_path / "job1" / "a.pdf").read_bytes() == b"%PDF-1.4"
