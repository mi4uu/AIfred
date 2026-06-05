"""FastAPI core (I.web). health + redacted config + (optional) chat API.

Wire an agent + ConfirmManager via create_app(agent=..., confirm=...) to expose
/api/chat etc. localhost-only CORS (C6). Frontend: web/ (bun/vite/Radix).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aifred import __version__
from aifred.config import get_settings
from aifred.confirm import ConfirmManager
from aifred.webapi import make_api


def create_app(
    agent: Any | None = None,
    confirm: ConfirmManager | None = None,
    whatsapp: Any | None = None,
    store: Any | None = None,
    triage: Any | None = None,
    contacts: Any | None = None,
) -> FastAPI:
    s = get_settings()
    app = FastAPI(title="AIfred", version=__version__)

    # C6: localhost only
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", f"http://{s.web_host}:{s.web_port}"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/config")
    def config() -> dict[str, object]:
        return get_settings().public_dict()  # V8 redacted

    if agent is not None:
        app.include_router(make_api(agent, confirm or ConfirmManager(), s.web_token, whatsapp, store, triage, contacts))

    # serve the built bun/vite/Radix UI if present (web/dist), so :9120 serves UI + API
    import os
    from pathlib import Path

    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
        os.environ.setdefault("AIFRED_UI", "served")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("aifred.main:app", host=s.web_host, port=s.web_port, reload=False)


if __name__ == "__main__":
    run()
