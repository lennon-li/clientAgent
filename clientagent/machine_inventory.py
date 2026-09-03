"""Credential-free, host-local availability inventory.

Inventory is deliberately weaker evidence than route eligibility.  It records
only what an allowlisted discovery command observed at a point in time and is
not a portable policy or an authorization source.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable


MACHINE_INVENTORY_VERSION = "machine-inventory/v1"


class InventoryError(ValueError):
    """Raised when trusted discovery configuration is invalid."""


class DiscoveryKind(StrEnum):
    EXECUTABLE_PRESENCE = "executable_presence"
    VERSION = "version"
    MODEL_AVAILABILITY = "model_availability"


@dataclass(frozen=True)
class DiscoveryCommand:
    command_id: str
    kind: DiscoveryKind | str
    subject: str
    executable: str
    argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandCapture:
    """Bounded transient output returned by the exact-command runner."""

    present: bool
    exit_code: int = -1
    stdout: bytes = b""
    stderr: bytes = b""
    output_truncated: bool = False
    complete_output_digest: str = ""


@dataclass(frozen=True)
class InventoryEntry:
    command_id: str
    kind: DiscoveryKind
    subject: str
    executable: str
    argv: tuple[str, ...]
    present: bool
    exit_code: int
    version: str = ""
    models: tuple[str, ...] = ()
    output_digest: str = ""
    output_truncated: bool = False


@dataclass(frozen=True)
class MachineInventory:
    version: str
    captured_at: str
    fresh_until: str
    entries: tuple[InventoryEntry, ...]
    digest: str = ""

    def is_fresh(self, now: datetime) -> bool:
        if not isinstance(now, datetime) or now.tzinfo is None:
            return False
        try:
            boundary = datetime.fromisoformat(self.fresh_until.replace("Z", "+00:00"))
        except ValueError:
            return False
        return now.astimezone(timezone.utc) < boundary


Runner = Callable[[DiscoveryCommand, Path, float, int], CommandCapture]


def _validate_command(command: DiscoveryCommand) -> DiscoveryKind:
    if not isinstance(command, DiscoveryCommand):
        raise InventoryError("commands must contain DiscoveryCommand values")
    for name in ("command_id", "subject", "executable"):
        value = getattr(command, name)
        if not isinstance(value, str) or not value.strip():
            raise InventoryError(f"{name} must be a non-empty string")
    try:
        kind = DiscoveryKind(command.kind)
    except (TypeError, ValueError) as exc:
        raise InventoryError("unsupported discovery kind") from exc
    if any(character in command.executable for character in " \t|&;<>()$`\\\n\r"):
        raise InventoryError("executable must be one exact path or program name")
    if not isinstance(command.argv, (tuple, list)) or any(
        not isinstance(argument, str) or "\x00" in argument or "\n" in argument or "\r" in argument
        for argument in command.argv
    ):
        raise InventoryError("argv must contain single-line strings")
    return kind


def _drain(stream, limit: int, target: dict[str, object], key: str) -> None:
    retained = bytearray()
    digest = hashlib.sha256()
    truncated = False
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        room = max(0, limit - len(retained))
        retained.extend(chunk[:room])
        truncated = truncated or len(chunk) > room
    target[key] = (bytes(retained), digest.digest(), truncated)


def _run_exact(command: DiscoveryCommand, working_directory: Path, timeout_seconds: float, max_output_bytes: int) -> CommandCapture:
    """Run exact argv without a shell or ambient credentials/configuration."""

    search_path = os.environ.get("PATH", os.defpath)
    executable = shutil.which(command.executable, path=search_path)
    if executable is None:
        return CommandCapture(present=False)
    if DiscoveryKind(command.kind) is DiscoveryKind.EXECUTABLE_PRESENCE:
        return CommandCapture(present=True)

    process = subprocess.Popen(
        [executable, *command.argv],
        cwd=working_directory,
        env={"PATH": search_path, "LANG": "C", "LC_ALL": "C"},
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None and process.stderr is not None
    captured: dict[str, object] = {}
    threads = (
        threading.Thread(target=_drain, args=(process.stdout, max_output_bytes, captured, "stdout"), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, max_output_bytes, captured, "stderr"), daemon=True),
    )
    for thread in threads:
        thread.start()
    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        for thread in threads:
            thread.join()
        raise InventoryError(f"discovery command timed out: {command.command_id}") from None
    for thread in threads:
        thread.join()
    stdout, stdout_hash, stdout_truncated = captured["stdout"]
    stderr, stderr_hash, stderr_truncated = captured["stderr"]
    # Preserve a digest of the complete drained streams while retaining only
    # bounded bytes. The raw output leaves this transient capture immediately.
    combined = hashlib.sha256(stdout_hash + stderr_hash).hexdigest()
    return CommandCapture(
        present=True,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        output_truncated=bool(stdout_truncated or stderr_truncated),
        complete_output_digest="sha256:" + combined,
    )


def _capture_digest(capture: CommandCapture) -> str:
    stdout_digest = hashlib.sha256(capture.stdout).digest()
    stderr_digest = hashlib.sha256(capture.stderr).digest()
    digest = hashlib.sha256(stdout_digest + stderr_digest)
    return "sha256:" + digest.hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def inventory_digest(inventory: MachineInventory) -> str:
    projection = replace(inventory, digest="")
    payload = json.dumps(asdict(projection), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def discover_machine_inventory(
    commands: tuple[DiscoveryCommand, ...],
    *,
    captured_at: datetime,
    freshness: timedelta,
    working_directory: str | Path,
    timeout_seconds: float = 10.0,
    max_output_bytes: int = 1024 * 1024,
    runner: Runner | None = None,
) -> MachineInventory:
    """Capture host-local availability from a trusted, exact-command allowlist.

    ``commands`` and ``runner`` are deployment-host inputs and must never be
    accepted from a client, project content, or an agent-authored file.
    """

    if not isinstance(commands, (tuple, list)) or not commands:
        raise InventoryError("commands must be non-empty")
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise InventoryError("captured_at must be timezone-aware")
    if not isinstance(freshness, timedelta) or freshness <= timedelta(0):
        raise InventoryError("freshness must be positive")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise InventoryError("timeout_seconds must be positive")
    if not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
        raise InventoryError("max_output_bytes must be positive")
    directory = Path(working_directory)
    if not directory.is_absolute() or not directory.is_dir():
        raise InventoryError("working_directory must be an existing absolute directory")
    execute = runner or _run_exact
    if not callable(execute):
        raise InventoryError("runner must be callable")

    entries: list[InventoryEntry] = []
    seen: set[str] = set()
    for command in commands:
        kind = _validate_command(command)
        if command.command_id in seen:
            raise InventoryError(f"duplicate command_id: {command.command_id}")
        seen.add(command.command_id)
        capture = execute(command, directory, float(timeout_seconds), max_output_bytes)
        if not isinstance(capture, CommandCapture):
            raise InventoryError("runner did not return CommandCapture")
        if not capture.present and (
            capture.exit_code != -1 or capture.stdout or capture.stderr
            or capture.output_truncated or capture.complete_output_digest
        ):
            raise InventoryError("an absent executable cannot have command output")
        complete_digest = capture.complete_output_digest
        if len(capture.stdout) > max_output_bytes or len(capture.stderr) > max_output_bytes:
            complete_digest = _capture_digest(capture)
            capture = replace(
                capture,
                stdout=capture.stdout[:max_output_bytes],
                stderr=capture.stderr[:max_output_bytes],
                output_truncated=True,
                complete_output_digest=complete_digest,
            )
        version = ""
        models: tuple[str, ...] = ()
        if capture.present and capture.exit_code == 0:
            decoded = capture.stdout.decode("utf-8", errors="replace")
            if kind is DiscoveryKind.VERSION:
                version = next((line.strip() for line in decoded.splitlines() if line.strip()), "")
            elif kind is DiscoveryKind.MODEL_AVAILABILITY:
                models = tuple(sorted({line.strip() for line in decoded.splitlines() if line.strip()}))
        entries.append(InventoryEntry(
            command_id=command.command_id,
            kind=kind,
            subject=command.subject,
            executable=command.executable,
            argv=tuple(command.argv),
            present=capture.present,
            exit_code=capture.exit_code,
            version=version,
            models=models,
            output_digest=(capture.complete_output_digest or _capture_digest(capture))
            if capture.present and kind is not DiscoveryKind.EXECUTABLE_PRESENCE else "",
            output_truncated=capture.output_truncated,
        ))

    instant = captured_at.astimezone(timezone.utc)
    inventory = MachineInventory(
        version=MACHINE_INVENTORY_VERSION,
        captured_at=_timestamp(instant),
        fresh_until=_timestamp(instant + freshness),
        entries=tuple(sorted(entries, key=lambda entry: entry.command_id)),
    )
    return replace(inventory, digest=inventory_digest(inventory))


__all__ = [
    "MACHINE_INVENTORY_VERSION", "CommandCapture", "DiscoveryCommand", "DiscoveryKind",
    "InventoryEntry", "InventoryError", "MachineInventory", "discover_machine_inventory",
    "inventory_digest",
]
