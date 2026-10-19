"""Books Advisor web: the reader app at / and the admin console at /admin."""
import os
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web import admin_api, user_api
from web.core import apply_settings, load_rag, load_settings

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Books Advisor")
app.include_router(user_api.router)
app.include_router(admin_api.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup():
    apply_settings(load_settings())
    threading.Thread(target=load_rag, daemon=True).start()


@app.get("/")
def user_page():
    return FileResponse(STATIC_DIR / "user.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
