from fastapi import FastAPI

app = FastAPI(
    title="Transaction Ledger API",
    description="API for managing accounts, transactions, balances, and transfers.",
    version="0.1.0",
)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Report that the service is running."""
    return {"status": "ok"}
