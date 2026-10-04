from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.api import admin, audit, openai_compat
from gateway.bootstrap import open_components_from_environment
from gateway.components import GatewayComponents

APP_TITLE = "AI Control Gateway"
HEALTH_OK_BODY = {"status": "ok"}


def create_app(components: GatewayComponents | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if components is not None:
            app.state.components = components
            yield
            return
        async with open_components_from_environment() as environment_components:
            app.state.components = environment_components
            yield

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.include_router(openai_compat.router)
    app.include_router(admin.router)
    app.include_router(audit.public_router)
    app.include_router(audit.protected_router)

    @app.get("/health")
    def get_health() -> dict[str, str]:
        return HEALTH_OK_BODY

    return app
