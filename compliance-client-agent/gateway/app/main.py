"""FastAPI application. Loopback only.

The gateway is never reachable from the network. It listens on 127.0.0.1 and
authenticates callers with a shared secret compared using hmac.compare_digest.
Browser-facing authentication is Open WebUI's problem, in front of it; see
docs/INGRESS.md.
"""

from __future__ import annotations

import asyncio
import getpass
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import db, gitops, schemas
from .config import Config, load_config
from .worker import Worker

log = logging.getLogger("compliance.gateway")

STATE: dict[str, Any] = {}


def _check_auth(cfg: Config, authorization: str | None) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    token = authorization.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not hmac.compare_digest(token, cfg.secret()):
        raise HTTPException(status_code=401, detail="Invalid credentials.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg: Config = app.state.cfg
    # Uvicorn only configures its own loggers, so the gateway's startup lines
    # (runtime identity, containment) would otherwise never be emitted.
    if not logging.getLogger("compliance").handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        logging.getLogger("compliance").setLevel(logging.INFO)
    cfg.gateway.log_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.gateway.db_path)
    worker = Worker(conn, cfg)
    STATE["conn"] = conn
    STATE["worker"] = worker
    worker.start()
    log.info("gateway ready on %s:%s", cfg.gateway.host, cfg.gateway.port)
    try:
        yield
    finally:
        await worker.stop()
        conn.close()


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(
        title="Compliance Client Agent Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.cfg = cfg

    # ------------------------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, Any]:
        conn = STATE.get("conn")
        worker: Worker | None = STATE.get("worker")
        repo_ok = (cfg.project.repo_path / "HEAD").exists()
        out: dict[str, Any] = {
            "status": "ok" if repo_ok else "degraded",
            "repo": str(cfg.project.repo_path),
            "repo_present": repo_ok,
            "current_job": worker.current_job_id if worker else None,
            "runtime_user": getpass.getuser(),
            "agent_provider": cfg.codex.provider,
            "agent_auth": worker.auth_mode if worker else "unknown",
        }
        if repo_ok:
            out["default_branch"] = gitops.detect_default_branch(
                cfg.project.repo_path, cfg.project.default_branch_fallback
            )
        if conn is not None:
            out["queued"] = len(
                conn.execute(
                    "SELECT 1 FROM jobs WHERE status = ?", (db.QUEUED,)
                ).fetchall()
            )
        return out

    # ------------------------------------------------------------------
    @app.post("/v1/jobs", status_code=202)
    async def create_job(
        request: Request, authorization: str | None = Header(default=None)
    ) -> JSONResponse:
        _check_auth(cfg, authorization)

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Body must be valid JSON.")

        # Server-owned overrides are rejected, never ignored.
        try:
            req = schemas.validate_body(body)
        except (schemas.ForbiddenField, schemas.UnknownField, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if len(req.message) > cfg.limits.max_message_chars:
            raise HTTPException(
                status_code=400,
                detail=f"message exceeds {cfg.limits.max_message_chars} characters.",
            )

        conn = STATE["conn"]
        chat = db.get_chat(conn, req.chat_id)
        if chat is None:
            db.create_chat(conn, req.chat_id, req.user_id)
        elif chat["status"] == db.CHAT_NEEDS_ATTENTION:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This chat is marked needs_attention after an earlier job. "
                    "Its worktree was left untouched on purpose. A maintainer "
                    "must inspect and clear it before new work can run."
                ),
            )

        job_id = db.create_job(
            conn, chat_id=req.chat_id, user_id=req.user_id, request=req.message,
            preview_requested=req.preview,
        )
        db.add_event(conn, job_id, "queued", {"user_id": req.user_id})
        STATE["worker"].notify()

        return JSONResponse(
            status_code=202,
            content=schemas.JobCreated(
                job_id=job_id,
                chat_id=req.chat_id,
                status=db.QUEUED,
                queue_position=db.queue_position(conn, job_id),
            ).model_dump(),
        )

    # ------------------------------------------------------------------
    @app.get("/v1/jobs/{job_id}")
    async def get_job(
        job_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _check_auth(cfg, authorization)
        row = db.get_job(STATE["conn"], job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No such job.")
        out = db.job_to_dict(row)
        if out["status"] == db.QUEUED:
            out["queue_position"] = db.queue_position(STATE["conn"], job_id)
        return out

    # ------------------------------------------------------------------
    @app.get("/v1/jobs/{job_id}/events")
    async def get_events(
        job_id: str,
        request: Request,
        after: int = 0,
        stream: bool = True,
        authorization: str | None = Header(default=None),
    ):
        """Server-Sent Events by default; `?stream=false` for a JSON snapshot.

        SSE was chosen over long-poll because Open WebUI's event emitter is
        already incremental, and because a dropped SSE connection has no effect
        on the job -- the events are rows in SQLite, and a reconnect just passes
        a higher `after` cursor.
        """
        _check_auth(cfg, authorization)
        conn = STATE["conn"]
        if db.get_job(conn, job_id) is None:
            raise HTTPException(status_code=404, detail="No such job.")

        if not stream:
            return {"job_id": job_id, "events": db.get_events(conn, job_id, after)}

        async def gen() -> AsyncIterator[bytes]:
            cursor = after
            while True:
                if await request.is_disconnected():
                    return
                for event in db.get_events(conn, job_id, cursor):
                    cursor = event["id"]
                    yield (
                        f"id: {event['id']}\n"
                        f"event: {event['kind']}\n"
                        f"data: {json.dumps(event)}\n\n"
                    ).encode()
                row = db.get_job(conn, job_id)
                if row is not None and row["status"] in (
                    db.SUCCEEDED, db.FAILED, db.NEEDS_ATTENTION
                ):
                    yield (
                        f"event: done\ndata: {json.dumps(db.job_to_dict(row))}\n\n"
                    ).encode()
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
