"""Smoke tests for the shared infrastructure layer.

These tests do not hit the network or any real LLM API — every external
call is mocked. They exercise the public surface of each shared module so
regressions in the plumbing are caught quickly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import openai
import pytest


# --- 1. config -----------------------------------------------------------

def test_config_missing_key_raises_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing OPENROUTER_API_KEY should raise ConfigError with a clear message."""
    from shared import config

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(config.ConfigError) as excinfo:
        config.get_openrouter_key()

    msg = str(excinfo.value)
    assert "OPENROUTER_API_KEY" in msg
    assert ".env" in msg


# --- 2. prompt_loader ----------------------------------------------------

def test_prompt_loader_renders_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """load_prompt should read a template and substitute kwargs via str.format."""
    from shared import prompt_loader

    prompts_root = tmp_path / "prompts"
    (prompts_root / "test").mkdir(parents=True)
    (prompts_root / "test" / "hello.txt").write_text(
        "Hi {name}, welcome to {place}.", encoding="utf-8"
    )

    monkeypatch.setattr(prompt_loader, "_PROMPTS_DIR", prompts_root)

    rendered = prompt_loader.load_prompt("test", "hello", name="World", place="Earth")
    assert rendered == "Hi World, welcome to Earth."

    # Missing variable -> friendly error, not raw KeyError.
    with pytest.raises(prompt_loader.PromptError) as excinfo:
        prompt_loader.load_prompt("test", "hello", name="World")
    assert "place" in str(excinfo.value)


# --- 3. storage ----------------------------------------------------------

def test_storage_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save_report -> load_report should round-trip data and inject _meta."""
    from shared import storage

    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(storage, "_REPORTS_DIR", reports_dir)

    payload = {"title": "Demo", "score": 87, "items": ["a", "b"]}
    report_id = storage.save_report(payload, report_type="audit")

    assert report_id.startswith("audit_")
    loaded = storage.load_report(report_id)

    assert loaded["title"] == "Demo"
    assert loaded["score"] == 87
    assert loaded["items"] == ["a", "b"]
    assert loaded["_meta"]["report_id"] == report_id
    assert loaded["_meta"]["report_type"] == "audit"
    assert loaded["_meta"]["created_at"]

    listed = storage.list_reports(report_type="audit")
    assert len(listed) == 1
    assert listed[0]["report_id"] == report_id


# --- 4. crawler cache ---------------------------------------------------

class _FakeResponse:
    def __init__(self, html: str) -> None:
        self.text = html
        self.headers = {"content-type": "text/html; charset=utf-8"}

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    """Minimal stand-in for ``httpx.Client`` used as a context manager."""

    call_count = 0

    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str) -> _FakeResponse:
        type(self).call_count += 1
        if "/about" in url:
            return _FakeResponse(
                "<html><head><title>About</title></head>"
                "<body><h1>About us</h1></body></html>"
            )
        return _FakeResponse(
            '<html><head><title>Home</title>'
            '<meta name="description" content="A demo site"></head>'
            '<body><a href="/about">About</a></body></html>'
        )


def test_crawler_uses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Second crawl of the same URL must hit the disk cache, not the network."""
    from shared import crawler

    monkeypatch.setattr(crawler, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(crawler.httpx, "Client", _FakeClient)
    _FakeClient.call_count = 0

    first = crawler.crawl_website("https://example.com", max_pages=3)
    assert first["metadata"].get("title") == "Home"
    assert first["metadata"].get("description") == "A demo site"
    assert first["about_html"]
    network_calls_after_first = _FakeClient.call_count
    assert network_calls_after_first >= 1

    second = crawler.crawl_website("https://example.com", max_pages=3)
    assert second["url"] == first["url"]
    # No additional network calls — entirely served from the cache.
    assert _FakeClient.call_count == network_calls_after_first


# --- 5. claude_client retries -------------------------------------------

def test_claude_client_retries_on_transient_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ask_claude must retry transient SDK errors and succeed on the third try."""
    from shared import claude_client

    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    transient = openai.APIConnectionError(message="connection reset", request=request)

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="hello world"))]
    fake_response.usage = MagicMock(prompt_tokens=12, completion_tokens=4)

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [transient, transient, fake_response]

    monkeypatch.setattr(claude_client, "_client", fake_client)
    monkeypatch.setattr(claude_client, "_BACKOFF_BASE_SECONDS", 0.0)
    claude_client.reset_usage_stats()

    result = claude_client.ask_claude("hi", model="haiku")

    assert result == "hello world"
    assert fake_client.chat.completions.create.call_count == 3

    stats = claude_client.get_usage_stats()
    assert stats["calls"] == 1
    assert stats["input_tokens"] == 12
    assert stats["output_tokens"] == 4
