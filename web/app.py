"""Books Advisor web: the reader app at /, the admin console at /admin, the API under /api (docs at /docs)."""
import os
import threading
from html import escape
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from web import admin_api, auth, chat_history, social_api, user_api
from web.core import apply_settings, load_rag, load_settings, logger

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Books Advisor",
    description="Book recommendations with RAG and a recommender. Sign in via /api/auth/login and send "
                "`Authorization: Bearer <access_token>`; renew it with /api/auth/refresh.",
)
app.include_router(auth.router)
app.include_router(user_api.router)
app.include_router(chat_history.router)
app.include_router(social_api.router)
app.include_router(admin_api.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def revalidate_pages(request, call_next):
    """The app's pages, scripts and styles change with every deploy: browsers must check for a new version
    (a cheap 304 when nothing changed) instead of guessing how long a cached copy is still good."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in ("/", "/admin", "/privacy"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.on_event("startup")
def startup():
    auth.ensure_admin()
    apply_settings(load_settings())
    try:
        social_api.sync_authors()
    except Exception as e:
        logger.error(f"Failed to sync authors from the catalog: {e}")
    threading.Thread(target=load_rag, daemon=True).start()


@app.get("/", include_in_schema=False)
def user_page():
    return FileResponse(STATIC_DIR / "user.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/privacy", include_in_schema=False)
def privacy_page():
    html = (Path(__file__).resolve().parent / "templates" / "privacy.html").read_text(encoding="utf-8")
    values = {
        "version": auth.CONSENT_VERSION,
        "operator": os.getenv("PRIVACY_OPERATOR", "the operator of this Books Advisor installation"),
        "contact": os.getenv("PRIVACY_CONTACT", "the administrator of this installation"),
    }
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", escape(value))
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
