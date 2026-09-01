"""GitHub Copilot CLI integration.

Copilot CLI is not command-line compatible with Codex.  It has no ``exec``
subcommand, uses ``--resume`` for durable sessions, and emits its own JSONL
session-event schema.  This module keeps that provider-specific behaviour out
of the gateway worker while preserving the worker's small result contract.

The outer bubblewrap jail remains the authoritative filesystem and privilege
boundary.  The Copilot CLI's automatic permissions are enabled only for the
jailed process; no path-widening or URL-widening flags are permitted here.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable, Sequence

from . import codex_runner
from .jail import JailSpec, build_bwrap_argv

# Reuse the gateway's stable result shape and maintainer-action marker.
CopilotResult = codex_runner.CodexResult

# These flags would defeat the Copilot permission and path model.  The
# bubblewrap jail is still required independently of these checks.
BANNED_ARGS = (
    "--yolo",
    "--allow-all",
    "--allow-all-paths",
    "--allow-all-urls",
)


def auth_mode() -> str:
    """Describe the non-secret credential state for health and startup logs."""
    return (
        "copilot-token-configured"
        if os.environ.get("COPILOT_GITHUB_TOKEN")
        else "copilot-oauth-or-token"
    )


def build_argv(
    *,
    binary: str,
    worktree: str,
    prompt: str,
    model: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    """Build a non-interactive Copilot invocation.

    Copilot CLI accepts the prompt as an option rather than stdin.  That is a
    limitation of its non-interactive interface; prompts contain no gateway
    credentials and are still absent from the gateway's own logs.
    """
    argv = [
        binary,
        "--no-auto-update",
        "--no-custom-instructions",
        "--no-remote",
        "--no-remote-export",
        "--no-color",
        "--silent",
        "--output-format",
        "json",
        "--no-ask-user",
        "--disable-builtin-mcps",
        # Non-interactive mode must be able to execute the normal coding tools.
        # The jail limits what those tools can reach.  Explicit deny rules keep
        # the two most important publication/escalation commands blocked too.
        "--allow-all-tools",
        "--deny-tool",
        "shell(git push)",
        "--deny-tool",
        "shell(sudo)",
        "-C",
        worktree,
    ]
    if model and model != "auto":
        argv += ["--model", model]
    if session_id:
        argv += ["--resume", session_id]
    argv += ["--prompt", prompt]
    assert_argv_safe(argv)
    return argv


def assert_argv_safe(argv: Sequence[str]) -> None:
    joined = " ".join(argv)
    for banned in BANNED_ARGS:
        # Check complete argv tokens: `--allow-all-tools` is intentionally
        # allowed for unattended jobs, while the broader `--allow-all` is not.
        if banned in argv:
            raise ValueError(f"Copilot argv contains banned argument {banned!r}")
    for required in ("--no-auto-update", "--no-custom-instructions", "-C",
                     "--output-format", "--prompt", "--allow-all-tools"):
        if required not in argv:
            raise ValueError(f"Copilot argv lacks {required!r}: {joined}")
    if argv[argv.index("--output-format") + 1] != "json":
        raise ValueError("Copilot output must be JSONL")
    if argv[argv.index("-C") + 1] == "":
        raise ValueError("Copilot working directory must not be empty")


def build_env(copilot_home: str, path: str, home: str | None = None) -> dict[str, str]:
    """Build a minimal Copilot environment without inheriting the parent env."""
    env = codex_runner.build_env(copilot_home, path, home)
    env.pop("CODEX_HOME", None)
    env["COPILOT_HOME"] = copilot_home
    env["COPILOT_AUTO_UPDATE"] = "false"

    # Only the explicitly named Copilot token is forwarded.  GH_TOKEN and
    # GITHUB_TOKEN are deliberately not copied from the gateway environment.
    token = os.environ.get("COPILOT_GITHUB_TOKEN", "")
    if token:
        env["COPILOT_GITHUB_TOKEN"] = token
    return env


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else event


def _extract_command(data: dict[str, Any]) -> str | None:
    arguments = data.get("arguments")
    if isinstance(arguments, dict):
        for key in ("command", "cmd", "script"):
            value = arguments.get(key)
            if isinstance(value, str):
                return value
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
        if isinstance(parsed, dict):
            return _extract_command({"arguments": parsed})
    return None


def _extract_tool_output(data: dict[str, Any]) -> str | None:
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if isinstance(content, str):
        return content
    blocks = result.get("contents")
    if isinstance(blocks, list):
        text: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key in ("text", "outputPreview"):
                if isinstance(block.get(key), str):
                    text.append(block[key])
        return "\n".join(text) if text else None
    return None


async def run_copilot(
    *,
    binary: str,
    worktree: str,
    prompt: str,
    codex_home: str,
    path: str,
    model: str | None = None,
    thread_id: str | None = None,
    timeout: int = 1800,
    on_event: Callable[[str, Any], Awaitable[None]] | None = None,
    jail: JailSpec | None = None,
    writable_extra: Sequence[str] = (),
) -> CopilotResult:
    """Run one Copilot turn and translate its JSONL events for the gateway."""
    argv = build_argv(
        binary=binary,
        worktree=worktree,
        prompt=prompt,
        model=model,
        session_id=thread_id,
    )
    if jail is not None:
        argv = build_bwrap_argv(jail) + argv

    env = build_env(codex_home, path)
    result = CopilotResult()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=worktree,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    stderr_chunks: list[str] = []
    message_buffers: dict[str, str] = {}

    async def pump_stderr() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            stderr_chunks.append(raw.decode("utf-8", "replace"))

    async def pump_stdout() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Diagnostics are retained in stderr; non-JSON stdout is not a
                # gateway event and must not be mistaken for agent content.
                continue
            result.raw_events.append(event)
            etype = str(event.get("type", ""))
            data = _event_data(event)

            if etype == "session.start":
                result.thread_id = data.get("sessionId")
                if on_event:
                    await on_event("thread_started", {
                        "thread_id": result.thread_id,
                        "provider": "copilot",
                    })
            elif etype == "assistant.message_delta":
                message_id = str(data.get("messageId", "default"))
                delta = data.get("deltaContent", "")
                if isinstance(delta, str):
                    message_buffers[message_id] = message_buffers.get(message_id, "") + delta
                    result.final_message = message_buffers[message_id]
                    if on_event:
                        await on_event("agent_message", {"text": delta})
            elif etype == "assistant.message":
                content = data.get("content", "")
                if isinstance(content, str) and content:
                    result.final_message = content
                    message_id = str(data.get("messageId", "default"))
                    message_buffers[message_id] = content
                    if on_event:
                        await on_event("agent_message", {"text": content})
            elif etype == "tool.execution_start":
                command = _extract_command(data)
                if command:
                    result.commands_run.append(command)
                    if on_event:
                        await on_event("command", {"command": command})
                elif on_event:
                    await on_event("tool_started", {
                        "tool": data.get("toolName", "unknown")
                    })
            elif etype == "tool.execution_complete":
                if on_event:
                    payload: dict[str, Any] = {"success": data.get("success")}
                    output = _extract_tool_output(data)
                    if output:
                        payload["output"] = output[-4000:]
                    await on_event("tool_completed", payload)
            elif etype in {
                "assistant.turn_start", "assistant.turn_end", "session.error",
                "session.warning", "assistant.idle",
            } and on_event:
                await on_event(etype.replace(".", "_"), data)

    try:
        await asyncio.wait_for(
            asyncio.gather(pump_stdout(), pump_stderr(), proc.wait()),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        result.timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        await proc.wait()

    result.exit_code = proc.returncode
    result.stderr = "".join(stderr_chunks)[-8000:]
    return result
