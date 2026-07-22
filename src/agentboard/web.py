from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Create the localhost dashboard API.

    Static dashboard delivery and SSE events are introduced in Milestone 3.
    """
    app = FastAPI(title="AgentBoard", docs_url=None, redoc_url=None)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "agentboard"}

    return app

