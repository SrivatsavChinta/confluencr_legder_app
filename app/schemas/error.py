from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Body of 404, 409, and 500 responses. 422 responses use FastAPI's validation error list instead."""

    detail: str
