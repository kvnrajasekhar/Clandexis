from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.core.config import settings
from app.api.v1 import api_router

@asynccontextmanager
async def lifespan(app):
    from app.services.confirmation import configure_confirmation
    from app.services.confirmation_ledger_adapter import ClaudeLedgerAdapter
    configure_confirmation(ClaudeLedgerAdapter())
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="Multimodal Financial Extraction & Business Validation API for Indian MSMEs",
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        docs_url=f"{settings.API_V1_STR}/docs",
        redoc_url=f"{settings.API_V1_STR}/redoc",
    )

    from app.core.security import install_security
    install_security(app)

    # Health check
    @app.get("/health", tags=["Health"])
    async def health_check():
        return {
            "status": "healthy",
            "service": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "default_model": settings.DEFAULT_MODEL,
        }

    # Mount API routes
    app.include_router(api_router, prefix=settings.API_V1_STR)

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
