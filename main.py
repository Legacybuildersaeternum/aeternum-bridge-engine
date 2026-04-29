from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from routes import users, admin, auth, messages, cohorts, origin_discovery, activation, guidance, movements, proofs, simulation
from services import registry

_ROOT = Path(__file__).resolve().parent

app = FastAPI(
    title="Aeternum Bridge Engine",
    description=(
        "MVP for the Diaspora Registry and Cultural Reconnection Engine. "
        "Register diaspora members, group them by family, and surface aggregate insights."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(users.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(messages.router)
app.include_router(cohorts.router)
app.include_router(origin_discovery.router)
app.include_router(activation.router)
app.include_router(guidance.router)
app.include_router(movements.router)
app.include_router(proofs.router)
app.include_router(simulation.router)

app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")


@app.on_event("startup")
def startup_persistence_check() -> None:
    """Confirm registry and activity persistence files are readable on startup."""
    registry.run_persistence_safety_check()


@app.get("/", tags=["UI"], include_in_schema=False)
def serve_ui() -> FileResponse:
    return FileResponse(_ROOT / "index.html", media_type="text/html")


if __name__ == "__main__":
    # Keep local launches pinned to this project even when called from another cwd.
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        app_dir=str(_ROOT),
    )
