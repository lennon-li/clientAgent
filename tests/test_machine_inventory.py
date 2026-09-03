from datetime import datetime, timedelta, timezone

import pytest

from clientagent.machine_inventory import (
    CommandCapture,
    DiscoveryCommand,
    DiscoveryKind,
    InventoryError,
    discover_machine_inventory,
    inventory_digest,
)


NOW = datetime(2026, 9, 3, 16, 0, 0, 123456, tzinfo=timezone.utc)


def _runner(command, working_directory, timeout_seconds, max_output_bytes):
    assert working_directory.is_absolute()
    assert timeout_seconds == 2
    assert max_output_bytes == 128
    if command.command_id == "missing":
        return CommandCapture(present=False)
    if command.kind == DiscoveryKind.VERSION:
        return CommandCapture(present=True, exit_code=0, stdout=b"tool 1.2\nignored\n")
    return CommandCapture(present=True, exit_code=0, stdout=b"model-z\nmodel-a\nmodel-z\n")


def _discover(tmp_path, commands):
    return discover_machine_inventory(
        commands,
        captured_at=NOW,
        freshness=timedelta(minutes=10),
        working_directory=tmp_path,
        timeout_seconds=2,
        max_output_bytes=128,
        runner=_runner,
    )


def test_inventory_is_normalized_bounded_evidence_not_raw_output(tmp_path):
    commands = (
        DiscoveryCommand("version", DiscoveryKind.VERSION, "tool", "/approved/tool", ("--version",)),
        DiscoveryCommand("models", DiscoveryKind.MODEL_AVAILABILITY, "models", "/approved/tool", ("models",)),
        DiscoveryCommand("missing", DiscoveryKind.EXECUTABLE_PRESENCE, "missing", "/approved/missing"),
    )
    inventory = _discover(tmp_path, commands)
    assert [entry.command_id for entry in inventory.entries] == ["missing", "models", "version"]
    assert inventory.entries[1].models == ("model-a", "model-z")
    assert inventory.entries[2].version == "tool 1.2"
    assert inventory.entries[0].present is False
    assert inventory.entries[0].output_digest == ""
    assert "ignored" not in repr(inventory)
    assert inventory.digest == inventory_digest(inventory)
    assert inventory.is_fresh(NOW + timedelta(minutes=9, microseconds=999999))
    assert not inventory.is_fresh(NOW + timedelta(minutes=10))

    reordered = _discover(tmp_path, tuple(reversed(commands)))
    assert reordered.digest == inventory.digest


def test_inventory_digest_changes_with_capture_time_or_observation(tmp_path):
    commands = (DiscoveryCommand("version", "version", "tool", "/approved/tool", ("--version",)),)
    inventory = _discover(tmp_path, commands)
    later = discover_machine_inventory(
        commands,
        captured_at=NOW + timedelta(seconds=1),
        freshness=timedelta(minutes=10),
        working_directory=tmp_path,
        timeout_seconds=2,
        max_output_bytes=128,
        runner=_runner,
    )
    assert later.digest != inventory.digest


def test_inventory_rejects_ambiguous_command_shapes_and_duplicate_ids(tmp_path):
    with pytest.raises(InventoryError, match="exact path"):
        _discover(tmp_path, (DiscoveryCommand("shell", "version", "shell", "sh -c", ("echo unsafe",)),))
    with pytest.raises(InventoryError, match="duplicate"):
        _discover(tmp_path, (
            DiscoveryCommand("same", "version", "one", "/approved/one"),
            DiscoveryCommand("same", "version", "two", "/approved/two"),
        ))
    with pytest.raises(InventoryError, match="timezone-aware"):
        discover_machine_inventory(
            (DiscoveryCommand("one", "version", "one", "/approved/one"),),
            captured_at=datetime(2026, 9, 3),
            freshness=timedelta(minutes=1),
            working_directory=tmp_path,
            runner=_runner,
        )


def test_inventory_records_failure_without_promoting_metadata(tmp_path):
    def failed(command, working_directory, timeout_seconds, max_output_bytes):
        return CommandCapture(present=True, exit_code=7, stdout=b"model-x\n", stderr=b"failure")

    inventory = discover_machine_inventory(
        (DiscoveryCommand("models", "model_availability", "models", "/approved/tool"),),
        captured_at=NOW,
        freshness=timedelta(minutes=1),
        working_directory=tmp_path,
        runner=failed,
    )
    entry = inventory.entries[0]
    assert entry.present is True
    assert entry.exit_code == 7
    assert entry.models == ()
    assert entry.output_digest
    assert not hasattr(entry, "eligible")


def test_inventory_defensively_bounds_custom_runner_output(tmp_path):
    def noisy(command, working_directory, timeout_seconds, max_output_bytes):
        return CommandCapture(present=True, exit_code=0, stdout=b"x" * 200, stderr=b"y" * 200)

    inventory = discover_machine_inventory(
        (DiscoveryCommand("version", "version", "tool", "/approved/tool"),),
        captured_at=NOW,
        freshness=timedelta(minutes=1),
        working_directory=tmp_path,
        max_output_bytes=16,
        runner=noisy,
    )
    entry = inventory.entries[0]
    assert entry.output_truncated is True
    assert len(entry.version) == 16
    assert entry.output_digest
