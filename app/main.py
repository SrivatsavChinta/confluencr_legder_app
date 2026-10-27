from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.database import engine, init_db


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


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Report that the service is running."""
    return {"status": "ok"}
