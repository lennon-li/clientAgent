"""Request validation and the client-override guard.

The client is a chat UI. It gets to say *who* is talking and *what* they said.
It does not get to say where the work happens, on what branch, with what
sandbox, or with whose credentials. Those are server-owned (app/config.py).

An attempt to supply any of them is a 400 with a named field, never a silent
drop -- a silently ignored `repo_path` looks like it worked, which is exactly
how a client ends up believing it can steer the server.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

#: The complete set of keys a client may send at the top level.
ALLOWED_KEYS = frozenset({"chat_id", "user_id", "message", "messages", "preview"})

#: Keys that are specifically server-owned. Present so the error message can
#: name the category, and so the guard is self-documenting.
FORBIDDEN_KEYS = frozenset({
    # repository / location
    "repo", "repo_path", "repository", "repo_url", "path", "cwd", "workdir",
    "working_dir", "directory", "dir", "project", "project_id", "project_path",
    "worktree", "worktree_path", "tree",
    # git
    "branch", "git_branch", "base_branch", "ref", "revision", "commit",
    "remote", "push", "force_push",
    # execution policy
    "sandbox", "sandbox_mode", "approval", "approval_policy", "approvals",
    "yolo", "dangerously_bypass_approvals_and_sandbox", "full_access",
    "codex_home", "codex_args", "command", "cmd", "shell", "exec",
    "timeout", "model", "system", "system_prompt", "bootstrap", "instructions",
    # credentials
    "secret", "token", "api_key", "apikey", "auth", "authorization",
    "password", "credentials", "key", "env", "environment",
})


class ForbiddenField(ValueError):
    """A client tried to supply a server-owned parameter."""

    def __init__(self, field: str, where: str) -> None:
        self.field = field
        self.where = where
        super().__init__(
            f"Field {field!r} (at {where}) is server-owned and cannot be set by "
            "a client. The gateway decides the repository, branch, worktree, "
            "sandbox, model, and credentials. Send only "
            "{chat_id, user_id, message, messages, preview}."
        )


class UnknownField(ValueError):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(
            f"Unknown field {field!r}. Allowed fields: "
            f"{', '.join(sorted(ALLOWED_KEYS))}."
        )


def _scan(value: Any, where: str, depth: int = 0) -> None:
    """Recursively reject server-owned keys anywhere in the payload.

    Nested scanning matters because `{"messages": [{"repo_path": ...}]}` is the
    obvious next attempt after the top-level check rejects the flat form.
    """
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, sub in value.items():
            k = str(key).strip().lower().replace("-", "_")
            if k in FORBIDDEN_KEYS:
                raise ForbiddenField(str(key), where or "body")
            _scan(sub, f"{where}.{key}" if where else str(key), depth + 1)
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            _scan(sub, f"{where}[{i}]", depth + 1)


def validate_body(body: Any) -> "JobRequest":
    """Validate a raw decoded JSON body into a JobRequest.

    Raises ForbiddenField / UnknownField / ValueError. The caller maps all of
    these to HTTP 400.
    """
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")

    # Server-owned keys first, so the error names the real problem rather than
    # reporting a generic "unknown field".
    _scan(body, "")

    for key in body:
        if key not in ALLOWED_KEYS:
            raise UnknownField(key)

    for required in ("chat_id", "user_id", "message"):
        if required not in body:
            raise ValueError(f"Missing required field {required!r}.")

    return JobRequest(**body)


class ChatMessage(BaseModel):
    role: str
    content: str


class JobRequest(BaseModel):
    """The only shape a client may send."""

    chat_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    messages: list[ChatMessage] | None = None
    preview: bool = False


class JobCreated(BaseModel):
    job_id: str
    chat_id: str
    status: str
    queue_position: int
