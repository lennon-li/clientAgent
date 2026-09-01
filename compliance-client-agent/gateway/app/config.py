"""Server-owned configuration loading.

Everything the gateway needs to decide *where* it works and *how* Codex is
invoked comes from config.yaml on disk plus environment variables. It never
comes from an HTTP request body. See app.schemas for the request-side guard.

RELOCATABILITY
--------------
No absolute path is baked into this module. The service tree root is resolved
from $COMPLIANCE_AGENT_HOME, falling back to the parent of the directory
holding config.yaml. Relative paths in config.yaml are joined to that root, so
`cp -a` to /srv/compliance-client-agent under a different UID needs no edits.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Environment variable naming the service tree root.
HOME_ENV_VAR = "COMPLIANCE_AGENT_HOME"

#: Environment variable holding the shared secret. Never stored in the repo.
SECRET_ENV_VAR = "COMPLIANCE_GATEWAY_SECRET"

#: Environment variable naming an alternate config.yaml.
CONFIG_ENV_VAR = "COMPLIANCE_GATEWAY_CONFIG"

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def default_config_path() -> Path:
    """Locate config.yaml without hardcoding an absolute path."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "config.yaml"


def resolve_home(config_path: Path) -> Path:
    """Resolve the service tree root.

    $COMPLIANCE_AGENT_HOME wins; otherwise the tree root is the parent of the
    directory containing config.yaml (i.e. <tree>/gateway/config.yaml -> <tree>).
    """
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        return Path(override).resolve()
    return config_path.resolve().parents[1]


def _path(home: Path, value: str) -> Path:
    """Join a config value to the tree root unless it is already absolute."""
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else (home / p)


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    port: int
    state_dir: Path
    db_path: Path
    worktrees_dir: Path
    log_dir: Path


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    repo_path: Path
    source_repo: Path
    default_branch_fallback: str
    branch_prefix: str
    test_command: str
    memory_index: str
    agents_md: str
    excluded_paths: tuple[str, ...]


@dataclass(frozen=True)
class CodexConfig:
    # Retained as `CodexConfig` for backwards compatibility with the existing
    # config object and database schema. `provider` selects the runner.
    provider: str
    binary: str
    codex_home: Path
    sandbox: str
    model: str
    timeout_seconds: int
    path: str

    def resolved_binary(self) -> str:
        """Absolute path to the configured agent executable."""
        if os.path.sep in self.binary:
            return self.binary
        found = shutil.which(self.binary, path=self.path)
        return found or self.binary


@dataclass(frozen=True)
class JailConfig:
    enabled: bool
    required: bool
    extra_ro: tuple[Path, ...]
    setenv: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Limits:
    max_message_chars: int
    max_history_messages: int


@dataclass(frozen=True)
class PreviewConfig:
    enabled: bool
    target_dir: Path
    url: str


@dataclass(frozen=True)
class Config:
    home: Path
    gateway: GatewayConfig
    project: ProjectConfig
    codex: CodexConfig
    jail: JailConfig
    limits: Limits
    preview: PreviewConfig
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def secret(self) -> str:
        """Return the shared secret from the environment.

        Raises RuntimeError if unset, so the service refuses to run
        unauthenticated rather than silently accepting every request.
        """
        value = os.environ.get(SECRET_ENV_VAR, "")
        if not value:
            raise RuntimeError(
                f"{SECRET_ENV_VAR} is not set. Refusing to run without a shared "
                "secret. See .env.example."
            )
        return value

    def runtime_paths(self) -> dict[str, Path]:
        """Every path the RUNNING gateway may touch.

        `project.source_repo` is deliberately absent: it is bootstrap-only.
        test_repo_isolation.py asserts none of these resolves inside the
        maintainer's checkout.
        """
        return {
            "state_dir": self.gateway.state_dir,
            "db_path": self.gateway.db_path,
            "worktrees_dir": self.gateway.worktrees_dir,
            "log_dir": self.gateway.log_dir,
            "repo_path": self.project.repo_path,
            "codex_home": self.codex.codex_home,
        }


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    config_path = Path(path) if path else default_config_path()
    import yaml  # local import keeps this module importable without PyYAML for typing

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    home = resolve_home(config_path)

    g = data["gateway"]
    host = os.environ.get("COMPLIANCE_GATEWAY_HOST", str(g["host"]))
    if host not in _LOOPBACK:
        # Enforced in code, not only in docs: a config typo must not turn this
        # into a network service. LAN delivery is via an nginx proxy in front
        # of Open WebUI (docs/INGRESS.md), never by binding this port wide.
        raise ValueError(
            f"gateway.host must be loopback (got {host!r}). The gateway is "
            "never exposed directly; see docs/INGRESS.md."
        )
    port = int(os.environ.get("COMPLIANCE_GATEWAY_PORT", g["port"]))

    p = data["project"]
    c = data["codex"]
    lim = data["limits"]
    j = data.get("jail") or {}
    preview = data.get("preview") or {}
    preview_target = _path(
        home,
        os.environ.get(
            "COMPLIANCE_PREVIEW_DIR",
            str(preview.get("target_dir", "/srv/shiny-server/test/compliance")),
        ),
    )
    if preview_target == Path("/srv/shiny-server/compliance"):
        raise ValueError("preview target may not be the live compliance app")

    provider = os.environ.get(
        "COMPLIANCE_AGENT_PROVIDER", str(c.get("provider", "codex"))
    ).strip().lower()
    if provider not in {"codex", "copilot"}:
        raise ValueError(
            f"unsupported agent provider {provider!r}; use 'codex' or 'copilot'"
        )
    binary_env = (
        "COMPLIANCE_COPILOT_BIN" if provider == "copilot"
        else "COMPLIANCE_CODEX_BIN"
    )
    path_env = (
        "COMPLIANCE_COPILOT_PATH" if provider == "copilot"
        else "COMPLIANCE_CODEX_PATH"
    )

    return Config(
        home=home,
        gateway=GatewayConfig(
            host=host,
            port=port,
            state_dir=_path(home, g["state_dir"]),
            db_path=_path(home, g["db_path"]),
            worktrees_dir=_path(home, g["worktrees_dir"]),
            log_dir=_path(home, g["log_dir"]),
        ),
        project=ProjectConfig(
            name=str(p["name"]),
            repo_path=_path(home, p["repo_path"]),
            source_repo=_path(
                home, os.environ.get("COMPLIANCE_SOURCE_REPO", p["source_repo"])
            ),
            default_branch_fallback=str(p["default_branch_fallback"]),
            branch_prefix=str(p["branch_prefix"]),
            test_command=str(p["test_command"]),
            memory_index=str(
                _path(
                    home,
                    os.environ.get(
                        "COMPLIANCE_MEMORY_INDEX", str(p["memory_index"])
                    ),
                )
            ),
            agents_md=str(p["agents_md"]),
            excluded_paths=tuple(str(x) for x in p["excluded_paths"]),
        ),
        codex=CodexConfig(
            provider=provider,
            binary=os.environ.get(binary_env, str(c["binary"])),
            codex_home=_path(home, c["codex_home"]),
            sandbox=str(c["sandbox"]),
            model=str(c["model"]),
            timeout_seconds=int(c["timeout_seconds"]),
            path=os.environ.get(path_env, str(c["path"])),
        ),
        jail=JailConfig(
            enabled=bool(j.get("enabled", True)),
            required=bool(j.get("required", True)),
            extra_ro=tuple(_path(home, str(x)) for x in (j.get("extra_ro") or ())),
            setenv=tuple((str(k), str(v)) for k, v in (j.get("setenv") or {}).items()),
        ),
        limits=Limits(
            max_message_chars=int(lim["max_message_chars"]),
            max_history_messages=int(lim["max_history_messages"]),
        ),
        preview=PreviewConfig(
            enabled=bool(preview.get("enabled", False)),
            target_dir=preview_target,
            url=os.environ.get(
                "COMPLIANCE_PREVIEW_URL",
                str(preview.get("url", "")),
            ),
        ),
        raw=data,
    )
