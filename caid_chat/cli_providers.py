"""Read-only WSL agent transports for CAID's structured reply contract."""

import json
import subprocess

try:
    from .ai import SCHEMA, _validate_result, context_messages, system_instructions
    from .process import run_command
except ImportError:
    from ai import SCHEMA, _validate_result, context_messages, system_instructions
    from process import run_command


def _prompt(messages, board_snapshot, schematic_snapshot, language,
            footprint_evidence, tool_context, tools_remaining):
    conversation = context_messages(messages, board_snapshot, schematic_snapshot,
                                    footprint_evidence, tool_context)
    return (system_instructions(language, tools_remaining) +
            "\nDo not use shell tools. Request KiCad reads through tool_requests. "
            "Return only the requested structured result.\n\nConversation and project data:\n" +
            json.dumps(conversation, ensure_ascii=False))


def _run(command, payload, provider, token):
    try:
        completed = run_command(command, input=payload, timeout=240, token=token)
    except FileNotFoundError:
        raise RuntimeError("WSL is not installed or the selected CLI was not found") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{provider} did not respond within four minutes") from None
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise RuntimeError(f"{provider} request failed: {detail}")
    return completed.stdout


def ask_cli(provider, model, messages, board_snapshot, schematic_snapshot=None,
            language="en", token=None, footprint_evidence=None, tool_context=None,
            tools_remaining=3):
    """Run an installed WSL CLI with project data on stdin and validate its reply."""
    if provider not in ("claude_cli", "agy_cli", "opencode_cli"):
        raise ValueError("Unsupported CLI provider")
    if not model.strip():
        raise ValueError("Select a model first")
    prompt = _prompt(messages, board_snapshot, schematic_snapshot, language,
                     footprint_evidence, tool_context, tools_remaining)
    schema = json.dumps(SCHEMA, separators=(",", ":"))
    if provider == "claude_cli":
        command = ["wsl.exe", "--exec", "bash", "-ic",
                   'cd /tmp && exec claude -p --tools "" --permission-mode plan --safe-mode '
                   '--no-session-persistence --output-format json --json-schema "$1" --model "$2"',
                   "caid", schema, model.strip()]
        output = _run(command, prompt, "Claude Code", token)
        try:
            envelope = json.loads(output)
        except ValueError:
            raise RuntimeError("Claude Code returned invalid JSON") from None
        if envelope.get("is_error") or envelope.get("subtype") != "success":
            raise RuntimeError("Claude Code did not complete: " + str(envelope.get("result", "unknown"))[:300])
        result = envelope.get("structured_output")
    elif provider == "agy_cli":
        command = ["wsl.exe", "--exec", "bash", "-ic",
                   'cd /tmp && exec agy --input-format stream-json --output-format stream-json '
                   '--json-schema "$1" --mode plan --sandbox --model "$2"',
                   "caid", schema, model.strip()]
        payload = json.dumps({"event": "user", "message": {"content": [
            {"type": "text", "text": prompt}]}}, ensure_ascii=False) + "\n"
        output = _run(command, payload, "Antigravity", token)
        try:
            events = [json.loads(line) for line in output.splitlines() if line.strip()]
        except ValueError:
            raise RuntimeError("Antigravity returned invalid JSON events") from None
        results = [event["result"] for event in events if event.get("event") == "result"
                   and isinstance(event.get("result"), dict)]
        if not results:
            raise RuntimeError("Antigravity returned no final result")
        envelope = results[-1]
        if envelope.get("status") != "SUCCESS":
            raise RuntimeError("Antigravity did not complete: " + str(envelope.get("error", "unknown"))[:300])
        result = envelope.get("structured_output")
    else:
        command = ["wsl.exe", "--exec", "bash", "-ic",
                   'cd /tmp && exec opencode run --pure --agent plan --format json --model "$1"',
                   "caid", model.strip()]
        output = _run(command, prompt, "OpenCode", token)
        try:
            events = [json.loads(line) for line in output.splitlines() if line.strip()]
        except ValueError:
            raise RuntimeError("OpenCode returned invalid JSON events") from None
        errors = [event.get("error") for event in events if event.get("type") == "error"]
        if errors:
            error = errors[-1] or {}
            raise RuntimeError("OpenCode failed: " + str(error.get("data", {}).get("message", error))[:300])
        finishes = [event.get("part", {}) for event in events if event.get("type") == "step_finish"]
        if not finishes or finishes[-1].get("reason") != "stop":
            raise RuntimeError("OpenCode did not complete its answer")
        content = "".join(event.get("part", {}).get("text", "") for event in events
                          if event.get("type") == "text")
        try:
            result = json.loads(content)
        except ValueError:
            raise RuntimeError("OpenCode returned invalid JSON; select a model that follows CAID's schema") from None
    return _validate_result(result, language)
