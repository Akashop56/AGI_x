"""core/llm_sdk.py — small adapter for the two Google SDK generations.

Several older modules (agent planner/executor, auto-heal, a few document tools)
were written against the legacy `google-generativeai` API:

    import google.generativeai as genai
    genai.configure(api_key=KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
    text = model.generate_content(prompt).text

The headless Android build ships only the modern `google-genai` SDK — the
legacy package drags in gRPC, which does not build on Termux — so this module
re-implements that tiny surface on top of `google-genai`.

Prompts, model names and call sites are untouched: only the import line in each
module changes. The brain logic stays exactly where it was.
"""

from __future__ import annotations

from typing import Any

_api_key: str | None = None
_client: Any = None


def configure(api_key: str | None = None, **_kwargs: Any) -> None:
    """Legacy entry point: remember the key used by later calls.

    An empty key is accepted so callers can start up before the user has
    configured anything; the error surfaces on the first real request.
    """
    global _api_key, _client
    _api_key = (api_key or "").strip() or None
    _client = None


def _client_for(api_key: str | None = None):
    global _client
    key = api_key or _api_key
    if not key:
        raise RuntimeError(
            "No Gemini API key configured. Call genai.configure(api_key=...) "
            "or add ~/BrahmaAI/config/api_keys.json first."
        )
    if _client is None or api_key:
        from google import genai as _genai  # modern SDK

        _client = _genai.Client(api_key=key, http_options={"api_version": "v1beta"})
    return _client


class _Response:
    """Minimal stand-in for the legacy response object."""

    def __init__(self, text: str):
        self.text = text

    def __str__(self) -> str:  # pragma: no cover - parity
        return self.text


class GenerativeModel:
    """Legacy-shaped wrapper around ``google.genai.Client``."""

    def __init__(
        self,
        model_name: str | None = None,
        system_instruction: str | None = None,
        api_key: str | None = None,
        **_kwargs: Any,
    ):
        self.model_name = model_name or "gemini-2.5-flash"
        self.system_instruction = system_instruction
        self._api_key = api_key

    def generate_content(self, contents: Any, **_kwargs: Any) -> _Response:
        client = _client_for(self._api_key)
        config: dict[str, Any] = {}
        if self.system_instruction:
            config["system_instruction"] = self.system_instruction
        result = client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config or None,
        )
        text = getattr(result, "text", None)
        if text is None:
            # Fall back to walking candidates for SDK versions that only fill those.
            chunks: list[str] = []
            for candidate in getattr(result, "candidates", None) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        chunks.append(part_text)
            text = "".join(chunks)
        return _Response(text or "")


def is_configured() -> bool:
    return bool(_api_key)
