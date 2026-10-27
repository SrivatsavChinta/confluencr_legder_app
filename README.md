# Transaction Ledger API

## Project Overview

The Transaction Ledger API is a backend service for recording financial accounts and the money that moves between them. It is being built incrementally in phases.

**Current phase (Phase 1):** project foundation only. The service exposes a single health-check endpoint. Accounts, transactions, balances, and transfers are planned for later phases and are not yet implemented.

## Technology Stack

Currently used:

- Python
- FastAPI (web framework, with Pydantic for validation)
- Uvicorn (ASGI server)
- pytest and FastAPI `TestClient` (testing; `httpx2` is the HTTP client `TestClient` uses under the hood)

Planned for later phases:

- SQLite (database)
- SQLAlchemy (ORM)

## Prerequisites

- Python 3.10 or newer (`python3 --version`)

## Local Setup

From the project root, create and activate a virtual environment (macOS):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Dependency Installation

With the virtual environment active, install the project along with its development dependencies:

```bash
pip install --upgrade pip
pip install -e ".[dev]"
```

To install only the runtime dependencies, use `pip install -e .`.

## Running the Application

```bash
uvicorn app.main:app --reload
```

The server starts at <http://127.0.0.1:8000>.

## Testing

```bash
pytest
```

## API Documentation

With the server running:

| Resource      | URL                            |
| ------------- | ------------------------------ |
| Health check  | <http://127.0.0.1:8000/health> |
| Swagger UI    | <http://127.0.0.1:8000/docs>   |
| ReDoc         | <http://127.0.0.1:8000/redoc>  |

Expected health check response:

```json
{"status": "ok"}
```
