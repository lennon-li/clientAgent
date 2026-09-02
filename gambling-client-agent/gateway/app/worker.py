"""The single-slot job worker.

Concurrency model
-----------------
Exactly one modifying job runs process-wide. There is no Celery and no Redis:
the queue is the `jobs` table (status='queued'), and one asyncio task drains it
in creation order. That keeps the queue durable across a restart and keeps two
Codex processes from ever sharing a worktree.

Disconnect safety
-----------------
`POST /v1/jobs` returns a job_id as soon as the row is written. Everything after
that happens here, in a task owned by the application, not by the request. A
client hanging up affects nothing: the Codex child is spawned with
`start_new_session=True`, so it does not share the server's process group and
never receives a signal aimed at the request.

Nothing in this module deletes, resets, or cleans a worktree. A job that fails
or is interrupted leaves the worktree exactly as it was and marks the chat
`needs_attention`, which blocks further jobs on that chat until a maintainer
looks at it. Automatic recovery here would mean automatic destruction of
whatever the last run left behind.
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import codex_runner, copilot_runner, db, gitops, preview
from .jail import JailSpec, bwrap_available
from .config import Config

log = logging.getLogger("gambling.worker")


class Worker:
    def __init__(self, conn: sqlite3.Connection, cfg: Config) -> None:
        self.conn = conn
        self.cfg = cfg
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.current_job_id: str | None = None
        self.runner = (
            copilot_runner if cfg.codex.provider == "copilot" else codex_runner
        )
        if cfg.codex.provider == "copilot":
            self.codex_home = codex_runner.CodexHome(
                path=cfg.codex.codex_home,
                is_maintainer_login=False,
                identity_note=(
                    "GitHub Copilot credential mode: "
                    f"{copilot_runner.auth_mode()}."
                ),
            )
            self.auth_mode = copilot_runner.auth_mode()
        else:
            self.codex_home = codex_runner.resolve_codex_home(
                cfg.codex.codex_home, Path.home() / ".codex"
            )
            self.auth_mode = (
                "maintainer-login"
                if self.codex_home.is_maintainer_login else "service-owned"
            )
        # Logged ONCE at startup. Deliberately not per job: the maintainer
        # login is the permanent runtime identity, so a per-job warning would
        # fire on every single job and become noise people learn to ignore.
        log.info(
            "runtime identity: uid=%s. %s",
            getpass.getuser(), self.codex_home.identity_note,
        )
        if cfg.jail.enabled and not bwrap_available():
            msg = ("jail.enabled is true but bwrap is not available at "
                   "/usr/bin/bwrap. Refusing to run the agent without containment.")
            if cfg.jail.required:
                raise RuntimeError(msg)
            log.error("%s (jail.required=false, continuing UNCONTAINED)", msg)
        log.info(
            "containment: %s",
            "bubblewrap (writes confined to worktree, service clone, CODEX_HOME)"
            if cfg.jail.enabled else "NONE -- agent runs uncontained",
        )

    def _codex_binaries(self) -> tuple[Path, ...]:
        """Everything Codex needs to execute, and nothing more.

        This took three attempts to get right, so the reasoning is recorded.

        ~/.local/bin/codex is a symlink to
        ~/.codex/packages/standalone/current/bin/codex, and `current` is itself
        a symlink to a versioned release directory. Codex spawns sibling
        helpers (codex-code-mode-host) at runtime, resolved relative to the
        path it was invoked through -- the `current` path, not the release
        path. Binding only the fully resolved release directory therefore
        produces a `codex` that starts and then fails the instant the agent
        tries to run a shell command.

        So walk the whole symlink chain and bind the parent directory at every
        link, giving both the `current/bin` and `releases/<v>/bin` paths.

        This exposes program files only. The credential (~/.codex/auth.json),
        session history, and config all live outside these directories and
        remain invisible inside the jail; test_jail.py asserts that.
        """
        binary = Path(self.cfg.codex.resolved_binary())
        paths: list[Path] = []

        link = binary
        for _ in range(10):  # bounded: never loop on a cyclic symlink
            paths.append(link)
            if link.parent.is_dir():
                paths.append(link.parent)
            if not link.is_symlink():
                break
            target = Path(os.readlink(link))
            link = target if target.is_absolute() else (link.parent / target)

        resolved = binary.resolve()
        paths.append(resolved)
        if resolved.parent.is_dir():
            paths.append(resolved.parent)

        return tuple(dict.fromkeys(p for p in paths if p.exists()))

    def _memory_binds(self) -> tuple[Path, ...]:
        """The project memory tree, read-only.

        The bootstrap instruction tells the agent to read MEMORY_INDEX.md
        first. Without this bind that file is invisible inside the jail and
        every job reports it missing -- which is exactly what happened before
        this was added. Bound read-only: the agent reads project context and
        cannot edit the memory.
        """
        index = Path(self.cfg.project.memory_index)
        return (index.parent,) if index.parent.is_dir() else ()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start draining the queue.

        GAMBLING_WORKER_DISABLED=1 keeps the loop from starting, so API tests
        can exercise queueing and validation without spawning a real Codex
        process (which costs money and edits a worktree).
        """
        if os.environ.get("GAMBLING_WORKER_DISABLED") == "1":
            log.warning("worker loop disabled via GAMBLING_WORKER_DISABLED")
            return
        self._task = asyncio.create_task(self._run(), name="gambling-worker")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def notify(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        # A job left 'running' by a crash is not resumable and must not be
        # silently retried: its worktree may be half-edited.
        for row in db.running_jobs(self.conn):
            db.update_job(
                self.conn,
                row["job_id"],
                status=db.NEEDS_ATTENTION,
                error="Gateway restarted while this job was running. The worktree "
                      "was left untouched and needs manual review.",
                maintainer_action_required=True,
                ended_at=db.now(),
            )
            db.update_chat(self.conn, row["chat_id"], status=db.CHAT_NEEDS_ATTENTION)
            log.warning("job %s marked needs_attention after restart", row["job_id"])

        while not self._stopping:
            job = db.next_queued_job(self.conn)
            if job is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                await self._process(job)
            except Exception:  # noqa: BLE001 - the loop must never die
                log.exception("worker failed on job %s", job["job_id"])
                db.update_job(
                    self.conn,
                    job["job_id"],
                    status=db.FAILED,
                    error="Internal worker error; see gateway logs.",
                    ended_at=db.now(),
                )
            finally:
                self.current_job_id = None

    # -- one job -----------------------------------------------------------

    async def _process(self, job: sqlite3.Row) -> None:
        job_id = job["job_id"]
        chat_id = job["chat_id"]
        self.current_job_id = job_id

        db.update_job(self.conn, job_id, status=db.RUNNING, started_at=db.now())
        await self._emit(job_id, "job_started", {"chat_id": chat_id})

        chat = db.get_chat(self.conn, chat_id)
        if chat is None:
            chat = db.create_chat(self.conn, chat_id, job["user_id"])

        if chat["status"] == db.CHAT_NEEDS_ATTENTION:
            db.update_job(
                self.conn, job_id,
                status=db.NEEDS_ATTENTION,
                error="This chat is marked needs_attention from an earlier job. A "
                      "maintainer must clear it before more work can run.",
                maintainer_action_required=True,
                ended_at=db.now(),
            )
            await self._emit(job_id, "blocked", {"reason": "chat needs_attention"})
            return

        repo = self.cfg.project.repo_path
        default_branch = gitops.detect_default_branch(
            repo, self.cfg.project.default_branch_fallback
        )

        # -- worktree: create on first message, reuse afterwards ------------
        is_new_thread = not chat["codex_thread_id"]
        if chat["worktree_path"] and Path(chat["worktree_path"]).exists():
            worktree = Path(chat["worktree_path"])
            branch = chat["git_branch"]
            await self._emit(job_id, "worktree_reused",
                             {"worktree": str(worktree), "branch": branch})
        else:
            branch = (
                f"{self.cfg.project.branch_prefix}/{db.short_chat_id(chat_id)}"
            )
            wt = await asyncio.to_thread(
                gitops.create_workspace,
                repo,
                self.cfg.gateway.worktrees_dir,
                branch,
                default_branch,
                self.cfg.project.excluded_paths,
            )
            worktree = wt.path
            db.update_chat(
                self.conn, chat_id,
                worktree_path=str(worktree), git_branch=branch,
            )
            await self._emit(job_id, "worktree_created", {
                "worktree": str(worktree),
                "branch": branch,
                "base_branch": default_branch,
                "base_commit": wt.base_commit,
                # Expected empty. Reported, not enforced -- see gitops.
                "excluded_paths_present": list(wt.excluded_present),
            })
            if wt.excluded_present:
                log.warning(
                    "worktree %s unexpectedly contains %s",
                    worktree, ", ".join(wt.excluded_present),
                )

        commit_before = gitops.head_commit(worktree)
        db.update_job(
            self.conn, job_id,
            worktree=str(worktree), branch=branch, commit_before=commit_before,
        )

        # -- prompt ---------------------------------------------------------
        thread_id = chat["codex_thread_id"]
        if is_new_thread or not thread_id:
            bootstrap = codex_runner.build_bootstrap(
                worktree=str(worktree),
                branch=str(branch),
                default_branch=default_branch,
                project=self.cfg.project.name,
                memory_index=self.cfg.project.memory_index,
                agents_md=self.cfg.project.agents_md,
                test_command=self.cfg.project.test_command,
            )
            prompt = f"{bootstrap}\n\n---\n\nCLIENT REQUEST:\n\n{job['request']}"
        else:
            prompt = job["request"]

        # -- run codex -------------------------------------------------------
        await self._emit(job_id, "agent_started", {
            "provider": self.cfg.codex.provider,
            "resume": bool(thread_id),
            "thread_id": thread_id,
        })

        async def on_event(kind: str, payload: Any) -> None:
            await self._emit(job_id, kind, payload)

        jail_spec = None
        if self.cfg.jail.enabled:
            jail_spec = JailSpec(
                worktree=worktree,
                codex_home=self.cfg.codex.codex_home,
                settings_src=(
                    self.cfg.codex.codex_home.parent.parent
                    / "gateway/copilot-settings.json"
                    if self.cfg.codex.provider == "copilot" else None
                ),
                # The credential is bound READ-ONLY on top of the writable
                # CODEX_HOME, so the agent cannot overwrite or delete it.
                auth_src=(
                    self.codex_home.path / "auth.json"
                    if (self.cfg.codex.provider == "codex"
                        and self.codex_home.is_maintainer_login) else None
                ),
                # The codex binary lives under ~/.local/bin, which is not
                # otherwise visible inside the jail. Codex also spawns sibling
                # helpers (codex-code-mode-host, ...), so bind every codex*
                # entry in that directory read-only -- surgical, rather than
                # exposing the whole of ~/.local/bin.
                extra_ro=(
                    self.cfg.jail.extra_ro
                    + self._codex_binaries()
                    + self._memory_binds()
                ),
                setenv=self.cfg.jail.setenv,
            )
            # Inside the jail CODEX_HOME is always the service path: the
            # maintainer's ~/.codex is not visible, only its auth.json is,
            # bound at the service location.
            await self._emit(job_id, "jail", {
                "engine": "bubblewrap",
                "writable": [str(p) for p in jail_spec.writable()],
            })

        run_method = (
            self.runner.run_copilot
            if self.cfg.codex.provider == "copilot"
            else self.runner.run_codex
        )
        result = await run_method(
            binary=self.cfg.codex.resolved_binary(),
            worktree=str(worktree),
            prompt=prompt,
            codex_home=str(
                self.cfg.codex.codex_home if jail_spec else self.codex_home.path
            ),
            path=self.cfg.codex.path,
            model=self.cfg.codex.model,
            thread_id=thread_id,
            timeout=self.cfg.codex.timeout_seconds,
            on_event=on_event,
            jail=jail_spec,
        )

        if result.thread_id and result.thread_id != thread_id:
            db.update_chat(self.conn, chat_id, codex_thread_id=result.thread_id)
        db.update_job(self.conn, job_id, codex_thread_id=result.thread_id or thread_id)

        # -- commit on the agent's behalf -----------------------------------
        # See gitops.commit_all for why this happens here and not in the agent.
        commit_error: str | None = None
        if not result.timed_out and result.exit_code in (0, None):
            try:
                summary = " ".join(job["request"].split())[:72]
                new_commit = await asyncio.to_thread(
                    gitops.commit_all,
                    worktree,
                    f"{summary}\n\nchat: {chat_id}\njob: {job_id}",
                )
                if new_commit:
                    await self._emit(job_id, "committed", {"commit": new_commit})
            except gitops.GitError as exc:
                commit_error = str(exc)
                log.warning("commit failed for job %s: %s", job_id, exc)

        # -- record what happened -------------------------------------------
        commit_after = gitops.head_commit(worktree)
        files = gitops.changed_files(worktree, commit_before)
        dirty = gitops.worktree_is_dirty(worktree)


        db.update_job(
            self.conn, job_id,
            commit_after=commit_after,
            files_changed=files,
            commands_run=result.commands_run,
            result=result.final_message,
            maintainer_action_required=result.maintainer_action_required,
            ended_at=db.now(),
        )

        if result.timed_out:
            status, err = db.NEEDS_ATTENTION, (
                f"{self.cfg.codex.provider} exceeded the "
                f"{self.cfg.codex.timeout_seconds}s timeout. The "
                "worktree was left exactly as it was and needs manual review."
            )
        elif result.exit_code not in (0, None):
            status, err = db.FAILED, (
                f"Codex exited {result.exit_code}. {result.stderr[-1000:]}"
            )
        else:
            status, err = db.SUCCEEDED, None

        # The gateway commits everything the agent changed, so a still-dirty
        # workspace here means the commit itself failed. That is worth a human
        # look: nothing is reset or deleted, the edits are preserved on disk.
        if status == db.SUCCEEDED and (dirty or commit_error):
            status = db.NEEDS_ATTENTION
            err = (
                "Job finished but its changes could not be committed"
                + (f": {commit_error}" if commit_error else "")
                + ". Nothing was reset or deleted; the edits are still in the "
                f"workspace. Inspect `git -C {worktree} status`."
            )

        preview_url: str | None = None
        preview_error: str | None = None
        if bool(job["preview_requested"]) and status == db.SUCCEEDED:
            try:
                preview_url = await asyncio.to_thread(
                    preview.render_and_publish,
                    worktree,
                    self.cfg.preview,
                    data_dir=self.cfg.project.source_repo / "agentData",
                    agent_data_dir=self.cfg.project.source_repo / "agentData",
                    codex_home=self.cfg.codex.codex_home,
                    extra_ro=self.cfg.jail.extra_ro,
                    setenv=self.cfg.jail.setenv,
                )
                await self._emit(job_id, "preview_ready", {"url": preview_url})
            except (OSError, RuntimeError) as exc:
                preview_error = str(exc)
                await self._emit(job_id, "preview_failed", {"error": preview_error})

        db.update_job(
            self.conn, job_id, status=status, error=err,
            preview_url=preview_url, preview_error=preview_error,
            maintainer_action_required=(
                result.maintainer_action_required
                or status == db.NEEDS_ATTENTION
                or preview_error is not None
            ),
        )
        if status == db.NEEDS_ATTENTION:
            db.update_chat(self.conn, chat_id, status=db.CHAT_NEEDS_ATTENTION)

        await self._emit(job_id, "job_finished", {
            "status": status,
            "commit_before": commit_before,
            "commit_after": commit_after,
            "files_changed": files,
            "commands_run": result.commands_run,
            "maintainer_action_required": result.maintainer_action_required,
            "error": err,
        })

    async def _emit(self, job_id: str, kind: str, payload: Any = None) -> None:
        db.add_event(self.conn, job_id, kind, payload)
