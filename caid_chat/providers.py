"""Model providers share CAID's validated reply contract."""

import json
import ipaddress
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

try:
    from .ai import SCHEMA, _validate_result, ask_codex, ask_openai, context_messages, system_instructions
    from .cli_providers import ask_cli
except ImportError:
    from ai import SCHEMA, _validate_result, ask_codex, ask_openai, context_messages, system_instructions
    from cli_providers import ask_cli


PROVIDERS = ("codex", "claude_cli", "agy_cli", "opencode_cli", "openai", "anthropic", "gemini", "ollama", "lmstudio")
CLI_PROVIDERS = frozenset(("codex", "claude_cli", "agy_cli", "opencode_cli"))
LOCAL_PROVIDERS = frozenset(("ollama", "lmstudio"))
DEFAULT_URLS = {"ollama": "http://127.0.0.1:11434", "lmstudio": "http://127.0.0.1:1234"}
DEFAULT_MODELS = {"codex": "gpt-6-sol", "claude_cli": "sonnet", "agy_cli": "gemini-3.8-flash-low",
                  "opencode_cli": "opencode/big-pickle",
                  "openai": "gpt-6-astra", "anthropic": "",
                  "gemini": "", "ollama": "", "lmstudio": ""}


def is_loopback_url(base_url):
    host = urlsplit(base_url.strip()).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _base_url(provider, base_url):
    value = (base_url or DEFAULT_URLS.get(provider, "")).strip().rstrip("/")
    parsed = urlsplit(value)
    if provider not in LOCAL_PROVIDERS or parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Enter a server URL such as http://127.0.0.1:11434")
    return value


def _json_request(url, *, body=None, api_key="", extra_headers=None, timeout=90, token=None):
    if token:
        token.check()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    if extra_headers:
        headers.update(extra_headers)
    request = Request(url, data=json.dumps(body).encode("utf-8") if body is not None else None,
                      headers=headers, method="POST" if body is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as stream:
            result = json.load(stream)
    except HTTPError as exc:
        detail = exc.read(350).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Model server unavailable: {exc}") from None
    if token:
        token.check()
    if not isinstance(result, dict):
        raise RuntimeError("Model server returned an invalid response")
    return result


def list_local_models(provider, base_url="", token=None, api_key=""):
    """Check server reachability and return model IDs without loading a model."""
    url = _base_url(provider, base_url)
    if provider == "ollama":
        result = _json_request(url + "/api/tags", api_key=api_key, timeout=15, token=token)
        models = result.get("models")
        if not isinstance(models, list):
            raise RuntimeError("Ollama did not return a model list")
        return [item["name"] for item in models if isinstance(item, dict) and isinstance(item.get("name"), str)]
    result = _json_request(url + "/v1/models", api_key=api_key, timeout=15, token=token)
    models = result.get("data")
    if not isinstance(models, list):
        raise RuntimeError("LM Studio did not return a model list")
    return [item["id"] for item in models if isinstance(item, dict) and isinstance(item.get("id"), str)]


def probe_model(provider, base_url, model, api_key="", token=None):
    """Verify that a model can obey CAID's current output contract."""
    result = ask_provider(provider, api_key, base_url, model,
                          [{"role": "user", "content": "Return a short greeting. Request no tools or changes."}],
                          {"document": "CAID connection test", "footprints": []},
                          token=token, tools_remaining=0)
    if (result["tool_requests"] or result["placements"] or result["footprint_updates"] or
            result["field_updates"] or result["net_renames"] or result["pin_connections"] or
            result["no_connect_markers"] or result["pin_disconnections"] or result["edit_schematic"]):
        raise RuntimeError("Model produced changes during the connection test")
    return True


def probe_local_model(provider, base_url, model, api_key="", token=None):
    """Compatibility alias for the local model probe."""
    return probe_model(provider, base_url, model, api_key, token)


def _merge_messages(messages):
    """Coalesce consecutive turns, including the appended KiCad context."""
    merged = []
    for item in messages:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"] += "\n\n" + item["content"]
        else:
            merged.append(dict(item))
    return merged


def _parse_reply(content, language):
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Model server returned no JSON answer")
    try:
        parsed = json.loads(content)
    except ValueError:
        raise RuntimeError("Model server returned invalid JSON; choose a model with structured output support") from None
    return _validate_result(parsed, language)


def _ask_anthropic(api_key, model, conversation, instructions, language, token):
    if not api_key:
        raise ValueError("Anthropic API key is missing")
    response = _json_request("https://api.anthropic.com/v1/messages", body={
        "model": model, "max_tokens": 4096, "system": instructions,
        "messages": _merge_messages(conversation),
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }, extra_headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"}, token=token)
    if response.get("stop_reason") != "end_turn":
        raise RuntimeError("Anthropic response did not finish: " + str(response.get("stop_reason", "unknown")))
    parts = response.get("content", [])
    content = "".join(part.get("text", "") for part in parts if isinstance(part, dict) and part.get("type") == "text")
    return _parse_reply(content, language)


def _ask_gemini(api_key, model, conversation, instructions, language, token):
    if not api_key:
        raise ValueError("Gemini API key is missing")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
        raise ValueError("Invalid Gemini model ID")
    contents = [{"role": "model" if item["role"] == "assistant" else "user",
                 "parts": [{"text": item["content"]}]} for item in _merge_messages(conversation)]
    response = _json_request(
        "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent",
        body={"systemInstruction": {"parts": [{"text": instructions}]}, "contents": contents,
              "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA}},
        extra_headers={"x-goog-api-key": api_key}, token=token)
    candidates = response.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidate: " + str(response.get("promptFeedback", {}).get("blockReason", "unknown")))
    candidate = candidates[0]
    if candidate.get("finishReason") != "STOP":
        raise RuntimeError("Gemini response did not finish: " + str(candidate.get("finishReason", "unknown")))
    parts = candidate.get("content", {}).get("parts", [])
    content = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    return _parse_reply(content, language)


def ask_provider(provider, api_key, base_url, model, messages, board_snapshot,
                 schematic_snapshot=None, language="en", token=None,
                 footprint_evidence=None, tool_context=None, tools_remaining=3):
    """Return one CAID reply; model output is validated before the UI sees it."""
    common = dict(footprint_evidence=footprint_evidence, tool_context=tool_context,
                  tools_remaining=tools_remaining)
    if provider == "codex":
        return ask_codex(model, messages, board_snapshot, schematic_snapshot, language, token, **common)
    if provider in ("claude_cli", "agy_cli", "opencode_cli"):
        return ask_cli(provider, model, messages, board_snapshot, schematic_snapshot,
                       language, token, **common)
    if provider == "openai":
        return ask_openai(api_key, model, messages, board_snapshot, schematic_snapshot, language, token, **common)
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown model provider: {provider}")
    if not model.strip():
        raise ValueError("Select a model first")
    instructions = system_instructions(language, tools_remaining)
    conversation = context_messages(messages, board_snapshot, schematic_snapshot,
                                    footprint_evidence, tool_context)
    if provider == "anthropic":
        return _ask_anthropic(api_key, model.strip(), conversation, instructions, language, token)
    if provider == "gemini":
        return _ask_gemini(api_key, model.strip(), conversation, instructions, language, token)
    base = _base_url(provider, base_url)
    conversation = [{"role": "system", "content": instructions}] + conversation
    if provider == "ollama":
        response = _json_request(base + "/api/chat", body={
            "model": model.strip(), "messages": conversation,
            "format": SCHEMA, "stream": False,
        }, api_key=api_key, token=token)
        content = response.get("message", {}).get("content")
    else:
        response = _json_request(base + "/v1/chat/completions", body={
            "model": model.strip(), "messages": conversation, "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "caid_reply", "strict": True, "schema": SCHEMA}},
        }, api_key=api_key, token=token)
        choices = response.get("choices", [])
        content = choices[0].get("message", {}).get("content") if choices else None
    return _parse_reply(content, language)
