"""
title: Gamble — Gambling Dashboard Agent
author: asgard
version: 0.1.0
license: MIT
description: >
  Gamble routes a chat message to the local gambling gateway, which runs a sandboxed
  Codex agent on a per-chat git worktree of the gambling project and streams
  progress back.
requirements: httpx
"""

# Installed and maintained through scripts/install_openwebui_pipe.py. Keep this
# source reviewed before applying updates to the Open WebUI Function.
#
# Design constraints this file deliberately honours:
#   * no shell execution
#   * no filesystem access
#   * no git
#   * no credentials beyond the gateway shared secret, read from the environment
#
# Everything the agent does happens on the other side of an HTTP call to
# 127.0.0.1. This module is a transport, and nothing else.

import json
import os
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
from pydantic import BaseModel, Field

# Terminal job states, mirroring app/db.py.
_TERMINAL = {"succeeded", "failed", "needs_attention"}


class Pipe:
    class Valves(BaseModel):
        gateway_url: str = Field(
            default=os.getenv("GAMBLING_GATEWAY_URL", "http://127.0.0.1:8643"),
            description="Loopback gateway base URL. Never a public address.",
        )
        gateway_secret: str = Field(
            default=os.getenv("GAMBLING_GATEWAY_SECRET", ""),
            description=(
                "Shared secret for the gateway. Read from the environment; "
                "never hardcode it here."
            ),
        )
        request_timeout: int = Field(
            default=1800,
            description="Seconds to wait for a job before giving up on the stream.",
        )

    def __init__(self) -> None:
        self.type = "pipe"
        self.id = "gambling_agent"
        self.name = "Gamble — Gambling Dashboard Agent"
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not self.valves.gateway_secret:
            raise RuntimeError(
                "GAMBLING_GATEWAY_SECRET is not set. The pipe will not send "
                "unauthenticated requests to the gateway."
            )
        return {
            "Authorization": f"Bearer {self.valves.gateway_secret}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _describe(kind: str, payload: dict[str, Any] | None) -> str | None:
        """Turn a gateway event into one line a non-technical client can read."""
        payload = payload or {}
        if kind == "queued":
            return "Queued."
        if kind == "worktree_created":
            return f"Created a working copy on branch {payload.get('branch', '?')}."
        if kind == "worktree_reused":
            return "Continuing in this chat's existing working copy."
        if kind == "codex_started":
            return "Resuming..." if payload.get("resume") else "Starting..."
        if kind == "command":
            cmd = str(payload.get("command", ""))
            return f"Running: {cmd[:120]}"
        if kind == "warning":
            return f"Warning: {payload.get('message', '')}"
        if kind == "job_finished":
            files = payload.get("files_changed") or []
            if payload.get("status") == "succeeded":
                return (
                    f"Done. {len(files)} file(s) changed."
                    if files else "Done. No files changed."
                )
            return f"Finished with status: {payload.get('status')}."
        return None

    # ------------------------------------------------------------------
    async def pipe(
        self,
        body: dict[str, Any],
        __user__: dict[str, Any] | None = None,
        __metadata__: dict[str, Any] | None = None,
        __event_emitter__: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AsyncIterator[str] | str:
        metadata = __metadata__ or {}
        user = __user__ or {}

        # chat_id and user id come from Open WebUI's reserved params, not from
        # the message. The gateway derives everything else itself -- sending it
        # a repo path or a branch here would be rejected with a 400 by design.
        chat_id = metadata.get("chat_id") or body.get("chat_id") or "unknown-chat"

        # Forward the REAL Open WebUI user id even when the browser login is a
        # single shared account. The gateway records it per job, so switching to
        # per-user accounts later needs no schema change and no pipe change.
        user_id = str(user.get("id") or user.get("email") or "unknown-user")

        messages = body.get("messages") or []
        message = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                message = m.get("content") or ""
                break
        if not message:
            return "No user message found."

        async def emit(description: str, done: bool = False) -> None:
            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {"description": description, "done": done},
                })

        preview = message.startswith("/preview")
        if preview:
            message = message[len("/preview"):].lstrip()
            if not message:
                return "Add the requested dashboard change after `/preview`."

        payload = {
            "chat_id": str(chat_id),
            "user_id": user_id,
            "message": message,
            "preview": preview,
        }

        try:
            headers = self._headers()
        except RuntimeError as exc:
            return f"Configuration error: {exc}"

        timeout = httpx.Timeout(self.valves.request_timeout, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                r = await client.post(
                    f"{self.valves.gateway_url}/v1/jobs",
                    headers=headers,
                    json=payload,
                )
            except httpx.RequestError as exc:
                return f"Could not reach the gambling gateway: {exc}"

            if r.status_code == 409:
                await emit("Blocked", done=True)
                return (
                    "This conversation is paused: an earlier job left its working "
                    "copy in a state that needs a maintainer to look at it. "
                    "Nothing was lost. Please start a new chat or ask the "
                    "maintainer to clear it."
                )
            if r.status_code >= 400:
                await emit("Rejected", done=True)
                return f"The gateway rejected the request ({r.status_code}): {r.text}"

            job_id = r.json()["job_id"]
            await emit("Queued...")

            # Stream progress. A dropped stream does not affect the job: it runs
            # server-side and the events are durable, so this can be re-read.
            final: dict[str, Any] | None = None
            try:
                async with client.stream(
                    "GET",
                    f"{self.valves.gateway_url}/v1/jobs/{job_id}/events",
                    headers=headers,
                ) as stream:
                    event_kind = ""
                    async for line in stream.aiter_lines():
                        if line.startswith("event:"):
                            event_kind = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            raw = line.split(":", 1)[1].strip()
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if event_kind == "done":
                                final = data
                                break
                            note = self._describe(
                                data.get("kind", event_kind), data.get("payload")
                            )
                            if note:
                                await emit(note)
            except httpx.RequestError:
                await emit("Lost the progress stream; fetching the result...")

            if final is None:
                r = await client.get(
                    f"{self.valves.gateway_url}/v1/jobs/{job_id}", headers=headers
                )
                final = r.json() if r.status_code == 200 else {}

        status = final.get("status", "unknown")
        await emit(f"Finished ({status}).", done=True)

        parts: list[str] = []
        if final.get("result"):
            parts.append(str(final["result"]))

        files = final.get("files_changed") or []
        if files:
            parts.append(
                "\n**Files changed:**\n"
                + "\n".join(f"- `{f}`" for f in files)
            )
        if final.get("commit_after") and final.get("commit_after") != final.get(
            "commit_before"
        ):
            parts.append(f"\n**Commit:** `{str(final['commit_after'])[:12]}`")
        if final.get("maintainer_action_required"):
            parts.append(
                "\n---\n**Maintainer action required.** The safe preparation is "
                "done, but finishing this needs someone with the right "
                "permissions."
            )
        if final.get("preview_url"):
            parts.append(
                "\n**Preview ready:**\n"
                "- Combined dashboard: "
                + str(final["preview_url"])
                + "gambling-dashboard.html\n"
                "- ConnexOntario: "
                + str(final["preview_url"])
                + "connexontario-gambling-dashboard.html\n"
                "- OSDUHS: "
                + str(final["preview_url"])
                + "osduhs-gambling-dashboard.html\n"
                "- Hospital gambling diagnoses (NACRS, DAD, and OMHRS): "
                + str(final["preview_url"])
                + "hospital-gambling-dashboard.html\n"
                "1. Open the combined dashboard, then use each data-source tab.\n"
                "2. Check the requested change, filters, charts, and download controls.\n"
                "3. Return here with anything unexpected; Gamble will revise the branch."
            )
        elif final.get("preview_requested") and final.get("preview_error"):
            parts.append("\n**Preview unavailable.** " + str(final["preview_error"]))
        if status in _TERMINAL - {"succeeded"} and final.get("error"):
            parts.append(f"\n---\n**Status: {status}.** {final['error']}")

        return "\n".join(parts) if parts else "The job produced no output."
