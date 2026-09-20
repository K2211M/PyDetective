"""
llm.py
------
Unified wrapper around the local Ollama HTTP API (default
http://localhost:11434). Used by:
    - interrogation.py  -> lightweight per-question calls to Llama (Stage 2)
    - case_generator.py -> one heavier offline call to Qwen (Stage 3)

Stdlib only (urllib) -- no extra pip installs needed on Termux/Pydroid 3.

Design note: this module raises typed exceptions instead of returning
None/empty-string on failure, so callers can't accidentally treat a
connection failure as "the character said nothing" -- they have to
handle it explicitly (see interrogation.py's try/except around every
call to ollama_chat()).
"""

import json
import socket
import urllib.error
import urllib.request

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 30  # seconds -- generous for CPU-only local inference


class LLMError(Exception):
    """Base class for any Ollama call failure."""


class LLMConnectionError(LLMError):
    """Couldn't reach the local Ollama server at all (not running? wrong host?)."""


class LLMTimeoutError(LLMError):
    """The local Ollama server didn't respond within the timeout window."""


def ollama_chat(model: str, system_prompt: str, user_message: str,
                 history: list[dict] | None = None, extra_context: str | None = None,
                 host: str = DEFAULT_HOST, timeout: float = DEFAULT_TIMEOUT) -> str:
    """
    Send one chat request to a local Ollama model and return the reply text.

    model         : Ollama model tag, e.g. "llama3" or "qwen2.5" (must match
                    `ollama list` exactly, including any ":tag" suffix)
    system_prompt : system-role content (persona + grounding instructions),
                    built once per interrogation session
    user_message  : the latest user turn (e.g. the detective's question)
    history       : prior turns as [{"role": "user"/"assistant", "content": ...}, ...],
                    inserted between the system prompt and user_message so
                    multi-turn conversations stay consistent
    extra_context : optional PER-TURN system-role note, inserted right
                    before user_message without touching system_prompt or
                    history -- e.g. interrogation.py's Stage 3.5 bluff-engine
                    directive (EVIDENCE_BACKED: True/False), which depends on
                    this specific question and shouldn't be baked into the
                    session's base system prompt
    host          : Ollama server base URL
    timeout       : seconds to wait before raising LLMTimeoutError

    Raises LLMConnectionError, LLMTimeoutError, or LLMError on failure.
    Never returns None -- either you get a reply string, or an exception.
    """
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    if extra_context:
        messages.append({"role": "system", "content": extra_context})
    messages.append({"role": "user", "content": user_message})

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{host}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except socket.timeout as exc:
        raise LLMTimeoutError(
            f"Ollama did not respond within {timeout}s (model='{model}')."
        ) from exc
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"Ollama returned HTTP {exc.code} for model '{model}': {body}") from exc
    except urllib.error.URLError as exc:
        # Covers connection-refused (server not running), DNS failures, etc.
        # A timed-out socket can surface here too on some platforms.
        if isinstance(exc.reason, socket.timeout):
            raise LLMTimeoutError(
                f"Ollama did not respond within {timeout}s (model='{model}')."
            ) from exc
        raise LLMConnectionError(
            f"Could not reach Ollama at {host}. Is `ollama serve` running? ({exc.reason})"
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Ollama returned invalid JSON: {raw[:200]!r}") from exc

    try:
        content = data["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LLMError(f"Unexpected Ollama response shape: {data}") from exc

    return content.strip()


def check_model_available(model: str, host: str = DEFAULT_HOST, timeout: float = 5) -> bool:
    """
    Quick, non-raising check of whether `model` is pulled and visible on the
    local Ollama server (via GET /api/tags). Returns False -- never raises --
    if the server is unreachable, so callers can use this for a friendly
    startup warning without extra error handling.
    """
    request = urllib.request.Request(f"{host}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except Exception:
        return False

    available = {m.get("name", "").split(":")[0] for m in data.get("models", [])}
    return model.split(":")[0] in available


def safe_chat(model: str, system_prompt: str, user_message: str, history: list[dict] | None,
              extra_context: str | None, label: str) -> str | None:
    """
    Error-handled wrapper around ollama_chat(), shared by interrogation.py
    and partner.py (Stage 3.5/4) so the same friendly failure messages
    don't get duplicated in every module that talks to a live model.
    Returns the reply text, or None (after printing a message) on any
    LLMError -- callers just check for None rather than handling three
    separate exception types themselves.
    """
    try:
        return ollama_chat(
            model=model, system_prompt=system_prompt, user_message=user_message,
            history=history, extra_context=extra_context,
        )
    except LLMConnectionError:
        print(f"\n[Can't reach the local Ollama server. Is `ollama serve` running, "
              f"and is '{model}' pulled? (ollama pull {model})]\n")
    except LLMTimeoutError:
        print(f"\n[{label} took too long to respond -- the local model may be "
              f"overloaded. Try a shorter message or try again.]\n")
    except LLMError as exc:
        print(f"\n[Something went wrong talking to the model: {exc}]\n")
    return None
