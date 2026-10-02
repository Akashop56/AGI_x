"""Mock API tests — the whole brain runs against scripted, offline responses.

Rules enforced by this module (no real key, no network):

* ``google.genai.Client`` is replaced by a recording fake, so every API call
  returns the standard mock text from :mod:`tests.mock_genai`;
* ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` are removed from the environment and
  the user data dir is redirected to a tmp path, so a real key on the machine
  can never be picked up;
* ``socket.connect`` is guarded, so any code path that tries to reach Google
  fails the test instead of silently making a request.

These tests exercise the real parsing/decision logic around each call site; only
the transport is mocked.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from mock_genai import (  # noqa: E402
    MOCK_API_KEY,
    NETWORK_BLOCK_MESSAGE,
    STANDARD_TEXT,
    canned_error_decision,
    canned_plan,
    install_client,
    json_response,
    parts_only_response,
    standard_text_response,
    text_response,
)

REPO_ROOT = TESTS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent.error_handler as error_handler  # noqa: E402
import agent.executor as executor  # noqa: E402
import agent.planner as planner  # noqa: E402
import core.llm_sdk as llm_sdk  # noqa: E402


# ---------------------------------------------------------------------------
# Offline guard rails
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def offline_sandbox(monkeypatch, tmp_path):
    """No real key, no real user config, no outbound sockets."""
    for variable in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(variable, raising=False)

    fake_home = tmp_path / "home"
    (fake_home / "BrahmaAI" / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home))
    monkeypatch.delenv("APPDATA", raising=False)

    # Modules cache the config path at import time; point them at the tmp dir.
    missing_key_file = fake_home / "BrahmaAI" / "config" / "api_keys.json"
    for module in (planner, executor, error_handler):
        monkeypatch.setattr(module, "API_CONFIG_PATH", missing_key_file, raising=False)

    # Reset the adapter's cached client so each test starts clean.
    monkeypatch.setattr(llm_sdk, "_client", None, raising=False)
    monkeypatch.setattr(llm_sdk, "_api_key", None, raising=False)

    # Hard offline guarantee.
    local_hosts = {"127.0.0.1", "::1", "localhost", ""}
    real_connect = socket.socket.connect

    def guard(address):  # pragma: no cover - only runs when something is wrong
        host = address[0] if isinstance(address, tuple) and address else address
        if str(host) not in local_hosts:
            raise AssertionError(f"{NETWORK_BLOCK_MESSAGE} (attempted: {address!r})")
        return real_connect(address)

    def guarded_connect(self, address):
        guard(address)

    def guarded_create_connection(address, *args, **kwargs):
        guard(address)
        raise AssertionError(NETWORK_BLOCK_MESSAGE)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    yield


@pytest.fixture
def fake_api(monkeypatch):
    """Install the fake SDK client; call it with scripted responses."""
    installed: list = []

    def _install(responses=None):
        fake = install_client(monkeypatch, responses)
        installed.append(fake)
        return fake

    return _install


def _write_key_file(monkeypatch, module, tmp_path, key: str = MOCK_API_KEY) -> Path:
    """Point ``module.API_CONFIG_PATH`` at a fake key file (still not a real key)."""
    path = tmp_path / f"{module.__name__.replace('.', '_')}_api_keys.json"
    path.write_text(json.dumps({"gemini_api_key": key}), encoding="utf-8")
    monkeypatch.setattr(module, "API_CONFIG_PATH", path, raising=False)
    return path


def test_network_guard_is_active():
    """The offline guard must be real, otherwise this suite proves nothing."""
    with pytest.raises(AssertionError, match="Outbound network access blocked"):
        socket.create_connection(("generativelanguage.googleapis.com", 443))


def test_no_real_api_key_is_present(monkeypatch):
    """A key on the developer's machine must never leak into these tests."""
    import os

    assert not (os.environ.get("GEMINI_API_KEY") or "").strip()
    assert not (os.environ.get("GOOGLE_API_KEY") or "").strip()
    assert planner._get_api_key() == ""


def test_real_sdk_client_cannot_escape_the_guard(monkeypatch):
    """The SDK's transport is intercepted by the guard (it uses create_connection)."""
    from google import genai

    def blocked(self, address, *args, **kwargs):
        raise AssertionError(f"offline guard stopped {address!r}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

    client = genai.Client(api_key=MOCK_API_KEY)
    with pytest.raises(AssertionError, match="offline guard stopped"):
        client.models.generate_content(model="gemini-2.5-flash", contents="hello")


# ---------------------------------------------------------------------------
# core.llm_sdk — the adapter every legacy call site depends on
# ---------------------------------------------------------------------------

def test_llm_sdk_needs_a_key_before_touching_the_network(fake_api):
    fake = fake_api()
    llm_sdk.configure(api_key=None)

    with pytest.raises(RuntimeError, match="No Gemini API key"):
        llm_sdk.GenerativeModel("gemini-2.5-flash").generate_content("hello")

    assert fake.models.calls == []
    assert fake.builds == []


def test_llm_sdk_returns_the_standard_mock_text(fake_api):
    fake = fake_api()
    llm_sdk.configure(api_key=MOCK_API_KEY)

    model = llm_sdk.GenerativeModel("gemini-2.5-flash", system_instruction="Be brief.")
    response = model.generate_content("What is the plan?")

    assert response.text == STANDARD_TEXT
    assert str(response) == STANDARD_TEXT

    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    assert call["contents"] == "What is the plan?"
    assert call["config"]["system_instruction"] == "Be brief."
    assert fake.api_key == MOCK_API_KEY
    assert fake.http_options == {"api_version": "v1beta"}


def test_llm_sdk_falls_back_to_candidate_parts(fake_api):
    fake_api([parts_only_response("Standard ", "mock ", "response from Brahma.")])
    llm_sdk.configure(api_key=MOCK_API_KEY)

    response = llm_sdk.GenerativeModel("gemini-2.5-flash").generate_content("hello")

    assert response.text == STANDARD_TEXT


def test_llm_sdk_rebuilds_the_client_when_the_key_changes(fake_api):
    fake = fake_api()
    llm_sdk.configure(api_key=MOCK_API_KEY)
    llm_sdk.GenerativeModel("gemini-2.5-flash").generate_content("one")
    first_client = llm_sdk._client

    llm_sdk.GenerativeModel("gemini-2.5-flash").generate_content("two")
    assert llm_sdk._client is first_client, "client should be cached between calls"

    llm_sdk.configure(api_key="another-fake-key")
    llm_sdk.GenerativeModel("gemini-2.5-flash").generate_content("three")
    assert len(fake.builds) == 2, "re-configuring must build a new client"


def test_llm_sdk_empty_configure_is_tolerated(fake_api):
    fake_api()
    llm_sdk.configure(api_key="   ")
    assert llm_sdk.is_configured() is False

    with pytest.raises(RuntimeError, match="No Gemini API key"):
        llm_sdk.GenerativeModel("gemini-2.5-flash").generate_content("hello")


# ---------------------------------------------------------------------------
# agent/planner.py — plan JSON parsing and fallbacks
# ---------------------------------------------------------------------------

def test_planner_reads_the_key_from_the_environment_first(monkeypatch, tmp_path):
    _write_key_file(monkeypatch, planner, tmp_path, key="from-file")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert planner._get_api_key() == "from-env"

    monkeypatch.delenv("GEMINI_API_KEY")
    assert planner._get_api_key() == "from-file"


def test_planner_without_any_key_returns_empty_string(monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "API_CONFIG_PATH", tmp_path / "missing.json", raising=False)
    assert planner._get_api_key() == ""


def test_create_plan_parses_the_mocked_plan(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, planner, tmp_path)
    fake = fake_api([canned_plan("standard mock goal")])

    plan = planner.create_plan("standard mock goal")

    assert [step["step"] for step in plan["steps"]] == [1, 2]
    assert plan["steps"][0]["tool"] == "web_search"
    # generated_code is rewritten before execution (brain guardrail).
    assert plan["steps"][1]["tool"] == "web_search"
    assert plan["steps"][1]["parameters"]["query"].startswith("Draft the answer")
    assert fake.models.models_used == ["gemini-2.5-flash"]


def test_create_plan_keeps_claude_code_for_website_goals(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, planner, tmp_path)
    fake_api([canned_plan("build me a website")])

    plan = planner.create_plan("build me a website")

    assert plan["steps"][1]["tool"] == "claude_code"


def test_create_plan_falls_back_when_every_model_errors(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, planner, tmp_path)
    fake = fake_api([RuntimeError("429 quota")] * 8)

    plan = planner.create_plan("summarize my notes")

    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["tool"] == "web_search"
    assert plan["steps"][0]["parameters"]["query"] == "summarize my notes"
    assert len(fake.models.calls) == 4, "one attempt per candidate model"


def test_create_plan_falls_back_on_unparsable_text(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, planner, tmp_path)
    fake_api([standard_text_response()])  # plain text, not JSON

    plan = planner.create_plan("anything")

    assert plan["steps"][0]["tool"] == "web_search"


def test_replan_includes_completed_and_failed_steps(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, planner, tmp_path)
    fake = fake_api([canned_plan("finish the job")])

    plan = planner.replan(
        goal="finish the job",
        completed_steps=[{"step": 1, "tool": "web_search"}],
        failed_step={"tool": "file_controller", "description": "write notes"},
        error="permission denied",
    )

    prompt = fake.models.last_prompt()
    assert "Step 1 (web_search): DONE" in prompt
    assert "permission denied" in prompt
    assert "Do not repeat completed steps." in prompt
    assert len(plan["steps"]) == 2


def test_replan_falls_back_when_the_api_is_unreachable(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, planner, tmp_path)
    fake_api([RuntimeError("mock outage")] * 8)

    plan = planner.replan("goal", [], {"tool": "web_search", "description": "x"}, "boom")

    assert plan["steps"][0]["tool"] == "web_search"


# ---------------------------------------------------------------------------
# agent/error_handler.py — recovery decisions
# ---------------------------------------------------------------------------

FAILED_STEP = {"step": 1, "tool": "web_search", "description": "look it up", "critical": False}


def test_analyze_error_parses_the_decision(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, error_handler, tmp_path)
    fake = fake_api([canned_error_decision("retry")])

    result = error_handler.analyze_error(FAILED_STEP, "timeout", attempt=1, max_attempts=3)

    assert result["decision"] is error_handler.ErrorDecision.RETRY
    assert result["max_retries"] == 1
    assert result["user_message"] == "Retrying with the standard mock response, sir."
    assert fake.models.calls[0]["model"] == "gemini-3.1-flash-lite"


def test_analyze_error_promotes_skip_to_replan_for_critical_steps(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, error_handler, tmp_path)
    fake_api([canned_error_decision("skip")])
    critical_step = dict(FAILED_STEP, critical=True)

    result = error_handler.analyze_error(critical_step, "boom", attempt=1, max_attempts=3)

    assert result["decision"] is error_handler.ErrorDecision.REPLAN
    assert "critical" in result["user_message"].lower()


def test_analyze_error_does_not_call_the_api_after_max_attempts(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, error_handler, tmp_path)
    fake = fake_api()

    result = error_handler.analyze_error(FAILED_STEP, "boom", attempt=5, max_attempts=2)

    assert result["decision"] is error_handler.ErrorDecision.REPLAN
    assert result["max_retries"] == 0
    assert fake.models.calls == []


def test_analyze_error_degrades_gracefully_without_a_key(fake_api, monkeypatch, tmp_path):
    monkeypatch.setattr(error_handler, "API_CONFIG_PATH", tmp_path / "missing.json", raising=False)
    fake = fake_api()

    result = error_handler.analyze_error(FAILED_STEP, "boom", attempt=1, max_attempts=3)

    assert result["decision"] is error_handler.ErrorDecision.REPLAN
    assert result["fix_suggestion"] == "Try alternative approach"
    assert fake.models.calls == [], "a missing key must fail before building a request"


def test_generate_fix_returns_a_runnable_replacement_step(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, error_handler, tmp_path)
    fake = fake_api([FakeCodeResponse()])

    step = error_handler.generate_fix(FAILED_STEP, "boom", "use another tool")

    assert step["tool"] == "claude_code"
    assert step["description"].startswith("Auto-fix for:")
    assert step["parameters"]["description"] == "use another tool"
    assert fake.models.calls[0]["model"] == "gemini-2.5-flash"


class FakeCodeResponse:
    """`generate_fix` reads ``response.text`` directly."""

    text = "```python\nprint('standard mock fix')\n```"


# ---------------------------------------------------------------------------
# agent/executor.py — summarising and language handling
# ---------------------------------------------------------------------------

def test_executor_summarize_uses_the_mocked_text(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, executor, tmp_path)
    fake = fake_api()
    spoken: list[str] = []

    summary = executor.AgentExecutor()._summarize(
        "standard mock goal",
        [{"description": "step one"}],
        speak=spoken.append,
    )

    assert summary == STANDARD_TEXT
    assert spoken == [STANDARD_TEXT]
    assert "standard mock goal" in fake.models.last_prompt()


def test_executor_summarize_falls_back_when_the_call_fails(fake_api, monkeypatch, tmp_path):
    monkeypatch.setattr(executor, "API_CONFIG_PATH", tmp_path / "missing.json", raising=False)
    fake_api()

    summary = executor.AgentExecutor()._summarize("goal", [], speak=None)

    assert "All done, sir." in summary


def test_detect_language_returns_the_mocked_answer(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, executor, tmp_path)
    fake_api([text_response("Turkish")])

    assert executor._detect_language("Merhaba") == "Turkish"


def test_detect_language_defaults_to_english_on_failure(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, executor, tmp_path)
    fake_api([RuntimeError("mock outage")])

    assert executor._detect_language("Merhaba") == "English"


def test_translate_skips_the_api_when_there_is_no_goal(fake_api):
    fake = fake_api()
    assert executor._translate_to_goal_language("content", "") == "content"
    assert fake.models.calls == []


def test_translate_uses_the_detected_language(fake_api, monkeypatch, tmp_path):
    _write_key_file(monkeypatch, executor, tmp_path)
    fake = fake_api([text_response("Turkish"), text_response("Standart sahte yanıt.")])

    translated = executor._translate_to_goal_language("Standard mock response.", "Merhaba dünya")

    assert translated == "Standart sahte yanıt."
    assert "into Turkish" in fake.models.last_prompt()
    assert fake.models.models_used == ["gemini-3.1-flash-lite", "gemini-3.1-flash-lite"]


# ---------------------------------------------------------------------------
# Actions that call the modern SDK directly
# ---------------------------------------------------------------------------

def test_web_search_gemini_path_reads_candidate_parts(fake_api, monkeypatch):
    import actions.web_search as web_search

    monkeypatch.setattr(web_search, "_get_api_key", lambda: MOCK_API_KEY)
    fake = fake_api([parts_only_response(STANDARD_TEXT)])

    assert web_search._gemini_search("standard mock query") == STANDARD_TEXT

    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    assert call["config"]["tools"] == [{"google_search": {}}]


def test_web_search_rejects_an_empty_model_answer(fake_api, monkeypatch):
    import actions.web_search as web_search

    monkeypatch.setattr(web_search, "_get_api_key", lambda: MOCK_API_KEY)
    fake_api([parts_only_response("")])

    with pytest.raises(ValueError, match="empty response"):
        web_search._gemini_search("standard mock query")


def test_docx_tools_model_runs_on_the_adapter(fake_api, monkeypatch):
    import actions.docx_tools as docx_tools

    fake = fake_api()
    monkeypatch.setattr(docx_tools, "_get_api_key", lambda: MOCK_API_KEY)

    model = docx_tools._gemini_client()
    assert model.generate_content("summarise this document").text == STANDARD_TEXT
    assert fake.models.calls[0]["model"] == "gemini-2.5-flash"


def test_file_processor_model_runs_on_the_adapter(fake_api, monkeypatch):
    import actions.file_processor as file_processor

    fake = fake_api()
    monkeypatch.setattr(file_processor, "_get_api_key", lambda: MOCK_API_KEY)

    model = file_processor._gemini_client()
    assert model.generate_content("describe this file").text == STANDARD_TEXT
    assert fake.models.calls[0]["model"] == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Dynamic skill execution (Project Ultron) — synthesis + repair
# ---------------------------------------------------------------------------

def test_skill_forge_synthesizer_parses_mocked_json(fake_api, monkeypatch):
    from core.skill_forge import SkillForge

    fake = fake_api([
        json_response({
            "manifest": {
                "name": "mock_skill",
                "aliases": ["mock"],
                "description": "Returns the standard mock response.",
                "triggers": ["mock skill"],
                "parameters": {"type": "OBJECT", "properties": {}, "required": []},
            },
            "code": "def execute(**kwargs):\n    return {'summary': 'standard mock response'}\n",
            "test_cases": [{"input": {}}],
        })
    ])
    monkeypatch.setattr("core.skill_forge._get_gemini_api_key", lambda: MOCK_API_KEY)

    result = SkillForge._call_llm_synthesizer("make a mock skill", "mock_skill", "")

    assert result["success"] is True
    assert result["manifest"]["name"] == "mock_skill"
    assert "def execute" in result["code"]
    assert fake.models.calls[0]["config"]["response_mime_type"] == "application/json"


def test_skill_forge_repair_returns_corrected_code(fake_api, monkeypatch):
    from core.skill_forge import SkillForge

    fake = fake_api([json_response({"code": "def execute(**kwargs):\n    return {}\n"})])
    monkeypatch.setattr("core.skill_forge._get_gemini_api_key", lambda: MOCK_API_KEY)

    result = SkillForge._repair_code("def execute(:\n", "SyntaxError", "repair it")

    assert result["success"] is True
    assert "def execute(**kwargs)" in result["code"]
    assert fake.models.calls[0]["config"]["response_mime_type"] == "application/json"
