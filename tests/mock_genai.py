"""Offline doubles for the Gemini SDKs — used by the API mock tests.

Why this file exists
--------------------
The brain talks to Google through two surfaces:

* the modern ``google-genai`` SDK (``genai.Client(...).models.generate_content``),
  used by the Live session, skill forging and most actions;
* the thin legacy-shaped adapter in :mod:`core.llm_sdk`, used by the planner,
  executor, error handler and document tools.

Neither may be reachable from the test suite: the sandbox has no API key, and
tests must never make a real request.  The doubles here replace
``google.genai.Client`` with a recording fake whose default answer is a
**standard text response** (``STANDARD_TEXT``), exactly what a mocked API call
is expected to return.

Usage
-----
    def test_something(monkeypatch, fake_api):
        client = fake_api()                      # default: STANDARD_TEXT
        ...
        assert client.models.calls[0]["model"] == "gemini-2.5-flash"

    def test_json_consumer(monkeypatch, fake_api):
        fake_api([json_response({"code": "print('hi')"})])

Endpoints that must return JSON get a JSON payload built around the same
standard text, so a single canned answer keeps every call site exercising real
parsing logic.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Optional

#: The canned answer every mocked API call returns unless a test scripts another.
STANDARD_TEXT = "Standard mock response from Brahma."

#: Obviously fake credentials — never a real key.
MOCK_API_KEY = "test-key-not-real"

#: Fails loudly if a test reaches the network instead of the doubles.
NETWORK_BLOCK_MESSAGE = (
    "Outbound network access blocked: API mock tests must run offline. "
    "Patch google.genai.Client (see tests/mock_genai.py) instead of calling the real API."
)


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

class FakePart:
    """Mirror of ``google.genai.types.Part`` for the fields callers read."""

    def __init__(self, text: str):
        self.text = text


class FakeContent:
    def __init__(self, parts: Iterable[str]):
        self.parts = [FakePart(text) for text in parts]


class FakeCandidate:
    def __init__(self, parts: Iterable[str]):
        self.content = FakeContent(parts)
        self.finish_reason = "STOP"


class FakeResponse:
    """Stand-in for a ``GenerateContentResponse``.

    ``text`` is ``None`` when the fake was built from parts only, which lets a
    test drive the ``response.candidates[0].content.parts`` fallback path in
    :mod:`actions.web_search` and :mod:`core.llm_sdk`.
    """

    def __init__(self, text: Optional[str] = None, parts: Optional[Iterable[str]] = None):
        part_list = list(parts) if parts is not None else ([text] if text else [])
        if text is None and part_list:
            text = "".join(part_list)
        self.text = text
        self.candidates = [FakeCandidate(part_list)] if part_list else []
        self.usage_metadata = {"total_token_count": 42}

    def __str__(self) -> str:
        return self.text or ""

    def __repr__(self) -> str:
        return f"FakeResponse(text={self.text!r}, candidates={len(self.candidates)})"


def standard_text_response() -> FakeResponse:
    """The default answer: a plain, standard text response."""
    return FakeResponse(STANDARD_TEXT)


def text_response(text: str) -> FakeResponse:
    """A response whose text is exactly ``text`` (no JSON quoting)."""
    return FakeResponse(text)


def json_response(payload: Any) -> FakeResponse:
    """A response whose text is a JSON document (for planner/forge style calls)."""
    return FakeResponse(json.dumps(payload, indent=2))


def parts_only_response(*chunks: str) -> FakeResponse:
    """A response with parts but no ``.text`` (some SDK versions behave this way)."""
    return FakeResponse(text=None, parts=list(chunks) or [STANDARD_TEXT])


# ---------------------------------------------------------------------------
# Recording client
# ---------------------------------------------------------------------------

class FakeModels:
    """Records every ``generate_content`` call and replays scripted answers."""

    def __init__(self, responses: Optional[Iterable[Any]] = None):
        self._responses: list[Any] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    # -- scripting helpers ---------------------------------------------------
    def queue(self, *responses: Any) -> None:
        self._responses.extend(responses)

    def _next(self, call: dict[str, Any]) -> FakeResponse:
        if not self._responses:
            return standard_text_response()
        nxt = self._responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        if callable(nxt):
            produced = nxt(call)
            return produced if isinstance(produced, FakeResponse) else FakeResponse(str(produced))
        return nxt

    # -- SDK surface ---------------------------------------------------------
    def generate_content(self, **kwargs: Any) -> FakeResponse:
        call = dict(kwargs)
        call.setdefault("model", None)
        call.setdefault("contents", None)
        call.setdefault("config", None)
        self.calls.append(call)
        return self._next(call)

    # Convenience for assertions
    @property
    def models_used(self) -> list[str]:
        return [call["model"] for call in self.calls]

    def last_prompt(self) -> str:
        contents = self.calls[-1]["contents"] if self.calls else ""
        if isinstance(contents, (list, tuple)):
            return " ".join(str(part) for part in contents)
        return str(contents)


class FakeGenAIClient:
    """Recording stand-in for ``google.genai.Client``."""

    def __init__(self, responses: Optional[Iterable[Any]] = None):
        self.api_key: Optional[str] = None
        self.http_options: Any = None
        self.builds: list[dict[str, Any]] = []
        self.models = FakeModels(responses)


def install_client(
    monkeypatch,
    responses: Optional[Iterable[Any]] = None,
    *,
    on_build: Optional[Callable[[FakeGenAIClient, dict], None]] = None,
) -> FakeGenAIClient:
    """Patch ``google.genai.Client`` so ``Client(...)`` returns a fake.

    Both SDK generations build clients through this name, so one patch covers
    ``core.llm_sdk``, ``actions/*`` and ``core/skill_forge``.
    """
    import google.genai as modern_sdk

    fake = FakeGenAIClient(responses=responses)

    def factory(*args: Any, **kwargs: Any) -> FakeGenAIClient:
        if args:
            fake.api_key = args[0]
        if "api_key" in kwargs:
            fake.api_key = kwargs["api_key"]
        if "http_options" in kwargs:
            fake.http_options = kwargs["http_options"]
        fake.builds.append(dict(kwargs))
        if on_build is not None:
            on_build(fake, kwargs)
        return fake

    monkeypatch.setattr(modern_sdk, "Client", factory)
    return fake


# ---------------------------------------------------------------------------
# Canned payloads used by several tests
# ---------------------------------------------------------------------------

PLAN_STEPS = [
    {
        "step": 1,
        "tool": "web_search",
        "description": "Research the request.",
        "parameters": {"query": "standard mock query"},
        "critical": True,
    },
    {
        "step": 2,
        "tool": "generated_code",
        "description": "Draft the answer with the standard mock text.",
        "parameters": {},
    },
]


def canned_plan(goal: str = "answer the user") -> FakeResponse:
    return json_response({"goal": goal, "steps": [dict(step) for step in PLAN_STEPS]})


def canned_error_decision(decision: str = "retry", **overrides: Any) -> FakeResponse:
    payload = {
        "decision": decision,
        "reason": "Transient failure in the mock suite.",
        "fix_suggestion": "Try the same step again.",
        "max_retries": 1,
        "user_message": "Retrying with the standard mock response, sir.",
    }
    payload.update(overrides)
    return json_response(payload)
