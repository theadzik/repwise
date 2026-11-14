"""The JSON API, and the one conversation that is not REST.

Who is asking is not decided here. oauth2-proxy sits in front, is the only way
in, and names the signed-in GitHub account in `X-Forwarded-User`. This listens
on localhost so that nothing else can set that header.
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from repwise_web import garmin
from repwise_web.db import (
    QUEUED,
    RUNNING,
    Athlete,
    GarminConnection,
    Run,
    Sessions,
    connect,
)
from repwise_web.settings import Settings

log = logging.getLogger(__name__)

#: How many codes one login may try before it has to start again.
CODE_ATTEMPTS = 5
#: WebSocket close code for a request that may not be made at all.
POLICY_VIOLATION = 1008


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AthleteOut(_Out):
    id: uuid.UUID
    github_login: str
    created_at: datetime


class RunSummary(_Out):
    id: uuid.UUID
    status: str
    exit_code: int | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunOut(RunSummary):
    output: str


class ConnectionOut(_Out):
    connected_at: datetime
    updated_at: datetime


def create_app(settings: Settings, sessions: Sessions | None = None) -> FastAPI:  # noqa: PLR0915 - one closure per route
    sessions = sessions or connect(settings.database_url)
    box = garmin.TokenBox(settings.token_keys)
    app = FastAPI(
        title="repwise",
        openapi_url="/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
    )

    @app.exception_handler(HTTPException)
    def problem(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {
                "title": HTTPStatus(exc.status_code).phrase,
                "status": exc.status_code,
                "detail": exc.detail,
            },
            status_code=exc.status_code,
            headers=exc.headers,
            media_type="application/problem+json",
        )

    def database() -> Iterator[Session]:
        with sessions() as session, session.begin():
            yield session

    def signed_in(x_forwarded_user: Annotated[str | None, Header()] = None) -> str:
        if not x_forwarded_user:
            raise HTTPException(HTTPStatus.UNAUTHORIZED, "Not signed in.")
        return x_forwarded_user.lower()

    Db = Annotated[Session, Depends(database)]
    Login = Annotated[str, Depends(signed_in)]

    def registered(login: Login, db: Db) -> Athlete:
        athlete = db.scalar(select(Athlete).where(Athlete.github_login == login))
        if athlete is None:
            raise HTTPException(
                HTTPStatus.NOT_FOUND, "No account yet. Register with PUT /api/v1/me."
            )
        return athlete

    Current = Annotated[Athlete, Depends(registered)]

    def json_only(request: Request) -> None:
        """Refuse a change that another site's page could have sent.

        A form on someone else's page can POST with the session cookie
        attached, but cannot label what it sends as JSON without the browser
        asking this server first, and nothing here answers that it may.
        """
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        sent = request.headers.get("content-type", "").partition(";")[0].strip()
        if sent != "application/json":
            raise HTTPException(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Send Content-Type: application/json.",
            )

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(json_only)])

    @api.get("/me")
    def me(current: Current) -> AthleteOut:
        return AthleteOut.model_validate(current)

    @api.put("/me")
    def register(login: Login, db: Db, response: Response) -> AthleteOut:
        """Create the account of whoever is signed in. Nothing if it exists."""
        created = db.scalar(
            insert(Athlete)
            .values(id=uuid.uuid4(), github_login=login)
            .on_conflict_do_nothing(index_elements=[Athlete.github_login])
            .returning(Athlete.id)
        )
        if created is not None:
            response.status_code = HTTPStatus.CREATED
            log.info("Registered %s", login)
        return AthleteOut.model_validate(registered(login, db))

    @api.post("/runs", status_code=HTTPStatus.CREATED)
    def start_run(current: Current, db: Db, response: Response) -> RunOut:
        if not garmin.is_connected(db, current.id):
            raise HTTPException(
                HTTPStatus.CONFLICT, "Not signed in to Garmin. Sign in first."
            )
        run = Run(athlete_id=current.id)
        try:
            # A savepoint, so that losing the race to another press leaves the
            # transaction fit to look up the run that won.
            with db.begin_nested():
                db.add(run)
        except IntegrityError:
            active = db.scalar(
                select(Run.id).where(
                    Run.athlete_id == current.id, Run.status.in_((QUEUED, RUNNING))
                )
            )
            raise HTTPException(
                HTTPStatus.CONFLICT,
                "A run is already queued or running.",
                headers={"Location": f"/api/v1/runs/{active}"} if active else None,
            ) from None
        response.headers["Location"] = f"/api/v1/runs/{run.id}"
        return RunOut.model_validate(run)

    @api.get("/runs")
    def list_runs(
        current: Current,
        db: Db,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[RunSummary]:
        """Runs, newest first, without their output."""
        runs = db.scalars(
            select(Run)
            .where(Run.athlete_id == current.id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
        return [RunSummary.model_validate(run) for run in runs]

    @api.get("/runs/{run_id}")
    def get_run(run_id: uuid.UUID, current: Current, db: Db) -> RunOut:
        run = db.get(Run, run_id)
        if run is None or run.athlete_id != current.id:
            raise HTTPException(HTTPStatus.NOT_FOUND, "No such run.")
        return RunOut.model_validate(run)

    @api.get("/garmin/connection")
    def garmin_connection(current: Current, db: Db) -> ConnectionOut:
        row = db.get(GarminConnection, current.id)
        if row is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, "Not signed in to Garmin.")
        return ConnectionOut.model_validate(row)

    @api.delete("/garmin/connection", status_code=HTTPStatus.NO_CONTENT)
    def sign_out_of_garmin(current: Current, db: Db) -> None:
        garmin.forget(db, current.id)
        log.info("Garmin: signed out")

    app.include_router(api)

    def athlete_id(login: str) -> uuid.UUID | None:
        with sessions() as db:
            return db.scalar(select(Athlete.id).where(Athlete.github_login == login))

    def keep(athlete: uuid.UUID, token: str) -> None:
        with sessions() as db, db.begin():
            garmin.store_login(db, box, athlete, token)

    @app.websocket("/api/v1/garmin/connection/login")
    async def garmin_login(socket: WebSocket) -> None:
        """Sign in to Garmin: the password, then the code if Garmin asks.

        One connection carries both steps, because what garminconnect holds
        between them cannot be stored. Nothing of a login that does not finish
        outlives its connection.

        What is sent is passed on and never logged: what reaches the log is
        which step it was and how it ended.
        """
        login = socket.headers.get("x-forwarded-user", "").lower()
        # A page on another site can open a WebSocket here. The browser says
        # where the page came from, and only our own may.
        origin = urlsplit(socket.headers.get("origin", "")).netloc
        athlete = None
        if login and origin and origin == socket.headers.get("host"):
            athlete = await run_in_threadpool(athlete_id, login)
        if athlete is None:
            await socket.close(POLICY_VIOLATION)
            return
        await socket.accept()
        try:
            token = await _converse(socket)
            if token is not None:
                await run_in_threadpool(keep, athlete, token)
                log.info("Garmin login: signed in")
                await socket.send_json({"step": "connected"})
            await socket.close()
        except WebSocketDisconnect:
            log.info("Garmin login: connection dropped")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(db: Db) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    if settings.static_dir:
        # Last, so that it answers only what no route above did.
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True))
    return app


async def _converse(socket: WebSocket) -> str | None:
    """Both steps of a login. The session it produced, or None if it failed."""
    form = await socket.receive_json()
    attempt = garmin.Login(str(form.get("email", "")), str(form.get("password", "")))
    try:
        wants_code = await run_in_threadpool(attempt.start)
    except Exception as exc:  # noqa: BLE001 - whatever it was, the page is where it gets read
        log.info("Garmin login: password step failed: %s", type(exc).__name__)
        await socket.send_json({"step": "failed", "detail": _said(exc)})
        return None
    if wants_code:
        await socket.send_json({"step": "code_required"})
        for _ in range(CODE_ATTEMPTS):
            code = str((await socket.receive_json()).get("code", ""))
            try:
                await run_in_threadpool(attempt.finish, code)
                break
            except Exception as exc:  # noqa: BLE001 - as above
                log.info("Garmin login: code step failed: %s", type(exc).__name__)
                await socket.send_json({"step": "code_rejected", "detail": _said(exc)})
        else:
            await socket.send_json(
                {"step": "failed", "detail": "Too many wrong codes. Start again."}
            )
            return None
    return attempt.token()


def _said(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
