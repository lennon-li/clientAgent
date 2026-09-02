from __future__ import annotations

import asyncio
from pathlib import Path

from app import copilot_runner as cr


def test_argv_uses_noninteractive_json_session_mode():
    argv = cr.build_argv(
        binary="copilot",
        worktree="/wt",
        prompt="inspect the package",
        model="auto",
    )
    assert argv[:2] == ["copilot", "--no-auto-update"]
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("-C") + 1] == "/wt"
    assert argv[argv.index("--prompt") + 1] == "inspect the package"
    assert "--experimental" in argv
    assert "--allow-all-tools" in argv
    for banned in cr.BANNED_ARGS:
        assert banned not in argv


def test_argv_resumes_a_copilot_session_without_dangerous_flags():
    argv = cr.build_argv(
        binary="copilot",
        worktree="/wt",
        prompt="continue",
        session_id="239cb807-92e7-41cd-8d54-4f0584f03361",
    )
    assert argv[argv.index("--resume") + 1] == "239cb807-92e7-41cd-8d54-4f0584f03361"
    assert "--allow-all-paths" not in argv
    assert "--allow-all-urls" not in argv


def test_env_forwards_only_the_explicit_copilot_token(monkeypatch):
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "token-for-test")
    monkeypatch.setenv("GH_TOKEN", "must-not-leak")
    env = cr.build_env("/service/copilot-home", "/usr/bin", home="/jail-home")
    assert env["COPILOT_HOME"] == "/service/copilot-home"
    assert env["COPILOT_AUTO_UPDATE"] == "false"
    assert env["COPILOT_GITHUB_TOKEN"] == "token-for-test"
    assert "GH_TOKEN" not in env
    assert "CODEX_HOME" not in env


def test_env_does_not_create_a_token_from_unrelated_environment(monkeypatch):
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    env = cr.build_env("/service/copilot-home", "/usr/bin")
    assert "COPILOT_GITHUB_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env


def test_jsonl_events_are_translated_and_session_is_durable(tmp_path: Path):
    executable = tmp_path / "fake-copilot"
    executable.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' "
        "'{\"type\":\"session.start\",\"data\":{\"sessionId\":\"sid-1\"}}' "
        "'{\"type\":\"tool.execution_start\",\"data\":{\"toolName\":\"shell\",\"arguments\":{\"command\":\"Rscript -e testthat::test_dir()\"}}}' "
        "'{\"type\":\"assistant.message_delta\",\"data\":{\"messageId\":\"m1\",\"deltaContent\":\"All tests pass\"}}' "
        "'{\"type\":\"assistant.message\",\"data\":{\"messageId\":\"m1\",\"content\":\"All tests pass\"}}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    async def exercise():
        events: list[tuple[str, object]] = []

        async def on_event(kind: str, payload: object) -> None:
            events.append((kind, payload))

        return await cr.run_copilot(
            binary=str(executable),
            worktree=str(tmp_path),
            prompt="test",
            codex_home=str(tmp_path / "home"),
            path="/usr/bin",
            timeout=10,
            on_event=on_event,
        ), events

    result, events = asyncio.run(exercise())
    assert result.exit_code == 0
    assert result.thread_id == "sid-1"
    assert result.final_message == "All tests pass"
    assert result.commands_run == ["Rscript -e testthat::test_dir()"]
    assert ("thread_started", {"thread_id": "sid-1", "provider": "copilot"}) in events
    assert any(kind == "command" for kind, _ in events)
