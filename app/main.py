import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import engine, init_db
from app.routers import accounts, transactions, transfers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(engine)
    yield


app = FastAPI(
    title="Transaction Ledger API",
    description="API for managing accounts, transactions, balances, and transfers.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(transfers.router)


@app.exception_handler(SQLAlchemyError)
def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    # Log the full error for operators; never send SQL or driver details to the client.
    # exc_info must be passed explicitly: this handler runs outside the original except block.
    logger.error("Database error while handling %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Report that the service is running."""
    return {"status": "ok"}
