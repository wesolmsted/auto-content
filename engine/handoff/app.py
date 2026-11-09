"""The hand-off web page (DESIGN.md §7.5): preview, save (= approve), reject, link post."""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import compare_digest
from typing import Annotated, Any

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engine.config import Settings
from engine.db.enums import Platform, PostStatus, RejectReason, RenderStatus
from engine.db.models import PostRow, RecipeRow, RenderRow, SnippetRow, SongRow
from engine.db.session import session_scope
from engine.handoff.approval import (
    LINKABLE,
    HandoffConflictError,
    HandoffNotFoundError,
    check_downloadable,
    get_render,
    link_post,
    record_download,
    reject_render,
)
from engine.handoff.links import PostUrlError
from engine.notify.handoff import recipe_short_name
from engine.storage.media_store import MediaKind, MediaStore

COOKIE_NAME = "ace_handoff"
COOKIE_MAX_AGE_S = 365 * 24 * 3600
NOT_SIGNED_IN = "Not signed in. Open the hand-off login link once on this device."

PLATFORM_LABELS = {Platform.IG_TRIAL: "Instagram Trial Reel", Platform.TIKTOK: "TikTok"}
POSTING_REMINDERS = {
    Platform.IG_TRIAL: "Turn on Trial before you share it.",
    Platform.TIKTOK: "Add the official sound if the song drives the post.",
}

HANDOFF_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=HANDOFF_DIR / "templates")


@dataclass(frozen=True)
class RenderSummary:
    id: int
    song_title: str
    style: str
    status: RenderStatus
    duration_s: float | None


@dataclass(frozen=True)
class TargetCard:
    platform: Platform
    label: str
    caption: str
    reminder: str
    post_status: PostStatus | None
    permalink: str | None
    link_error: str | None

    @property
    def linkable(self) -> bool:
        return self.post_status in LINKABLE


def create_app(
    settings: Settings,
    session_factory: sessionmaker[Session],
    store: MediaStore,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    app = FastAPI(title="Hand-off", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HANDOFF_DIR / "static"), name="static")
    signed_in_cookie = _cookie_value(settings.handoff_token)

    def is_signed_in(request: Request) -> bool:
        cookie = request.cookies.get(COOKIE_NAME)
        return (
            signed_in_cookie is not None
            and cookie is not None
            and compare_digest(cookie, signed_in_cookie)
        )

    @app.middleware("http")
    async def require_login(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/login" or is_signed_in(request):
            return await call_next(request)
        return PlainTextResponse(NOT_SIGNED_IN, status_code=401)

    @app.exception_handler(HandoffNotFoundError)
    async def not_found(request: Request, error: HandoffNotFoundError) -> Response:
        return PlainTextResponse(str(error), status_code=404)

    @app.exception_handler(HandoffConflictError)
    async def conflict(request: Request, error: HandoffConflictError) -> Response:
        return PlainTextResponse(str(error), status_code=409)

    @app.get("/login")
    def login(token: str = "") -> Response:
        if signed_in_cookie is None or not compare_digest(_digest(token), signed_in_cookie):
            return PlainTextResponse("Wrong or missing token.", status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            signed_in_cookie,
            max_age=COOKIE_MAX_AGE_S,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/")
    def index(request: Request) -> Response:
        with session_factory() as session:
            ready = session.scalars(
                select(RenderRow)
                .where(RenderRow.status == RenderStatus.READY)
                .order_by(RenderRow.created_at.desc(), RenderRow.id.desc())
            ).all()
            awaiting_post = session.scalars(
                select(RenderRow)
                .where(
                    RenderRow.status == RenderStatus.APPROVED,
                    RenderRow.id.in_(
                        select(PostRow.render_id).where(PostRow.status == PostStatus.AWAITING_POST)
                    ),
                )
                .order_by(RenderRow.downloaded_at.desc(), RenderRow.id.desc())
            ).all()
            context = {
                "ready": [_summary(session, render) for render in ready],
                "awaiting_post": [_summary(session, render) for render in awaiting_post],
            }
        return TEMPLATES.TemplateResponse(request, "index.html", context)

    @app.get("/handoff/{render_id}")
    def handoff_page(request: Request, render_id: int) -> Response:
        with session_factory() as session:
            return _handoff_page(request, session, get_render(session, render_id))

    @app.get("/handoff/{render_id}/preview")
    def preview(render_id: int) -> Response:
        with session_factory() as session:
            path = _video_path(store, get_render(session, render_id))
        return FileResponse(path, media_type="video/mp4")

    @app.get("/handoff/{render_id}/download")
    def download(render_id: int) -> Response:
        with session_scope(session_factory) as session:
            render = get_render(session, render_id)
            check_downloadable(render)
            path = _video_path(store, render)
            record_download(session, render, clock())
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.post("/handoff/{render_id}/reject")
    def reject(
        render_id: int,
        reason: Annotated[RejectReason, Form()],
        note: Annotated[str, Form()] = "",
    ) -> Response:
        with session_scope(session_factory) as session:
            reject_render(session, get_render(session, render_id), reason, note, clock())
        return RedirectResponse(f"/handoff/{render_id}", status_code=303)

    @app.post("/handoff/{render_id}/link")
    def link(
        request: Request,
        render_id: int,
        platform: Annotated[Platform, Form()],
        url: Annotated[str, Form()] = "",
    ) -> Response:
        with session_scope(session_factory) as session:
            render = get_render(session, render_id)
            try:
                link_post(session, render, platform, url)
            except PostUrlError as error:
                return _handoff_page(
                    request, session, render, status_code=400, link_errors={platform: str(error)}
                )
        return RedirectResponse(f"/handoff/{render_id}", status_code=303)

    return app


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cookie_value(token: SecretStr | None) -> str | None:
    """The cookie holds a hash of the token, so the token itself is only sent once, at login."""
    if token is None or not token.get_secret_value():
        return None
    return _digest(token.get_secret_value())


def _video_path(store: MediaStore, render: RenderRow) -> Path:
    if render.file_path is not None:
        path = store.path_for(MediaKind.RENDERS, Path(render.file_path).name)
        if path.is_file():
            return path
    raise HandoffNotFoundError(f"render {render.id} has no video file")


def _summary(session: Session, render: RenderRow) -> RenderSummary:
    snippet = session.get_one(SnippetRow, render.snippet_id)
    song = session.get_one(SongRow, snippet.song_id)
    recipe = session.get_one(RecipeRow, render.recipe_id)
    return RenderSummary(
        id=render.id,
        song_title=song.title,
        style=recipe_short_name(recipe.params_json),
        status=render.status,
        duration_s=render.duration_s,
    )


def _handoff_page(
    request: Request,
    session: Session,
    render: RenderRow,
    status_code: int = 200,
    link_errors: dict[Platform, str] | None = None,
) -> Response:
    posts = {
        post.platform: post
        for post in session.scalars(select(PostRow).where(PostRow.render_id == render.id))
    }
    cards = []
    for target in render.targets_json:
        platform = Platform(target)
        post = posts.get(platform)
        cards.append(
            TargetCard(
                platform=platform,
                label=PLATFORM_LABELS[platform],
                caption=render.captions_json.get(platform.value, ""),
                reminder=POSTING_REMINDERS[platform],
                post_status=post.status if post else None,
                permalink=post.permalink if post else None,
                link_error=(link_errors or {}).get(platform),
            )
        )
    context: dict[str, Any] = {
        "render": _summary(session, render),
        "cards": cards,
        "filename": Path(render.file_path or f"render_{render.id}.mp4").name,
        "can_save": render.status in {RenderStatus.READY, RenderStatus.APPROVED},
        "can_reject": render.status == RenderStatus.READY,
        "reject_reasons": list(RejectReason),
        "reject_reason": render.reject_reason,
        "reject_note": render.reject_note,
    }
    return TEMPLATES.TemplateResponse(request, "handoff.html", context, status_code=status_code)
