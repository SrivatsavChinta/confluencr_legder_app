import pytest

from app.main import app


@pytest.fixture
def schema():
    return app.openapi()


@pytest.mark.parametrize("model", ["TransactionCreate", "TransferCreate"])
def test_request_amount_is_documented_as_string_only(schema, model):
    amount = schema["components"]["schemas"][model]["properties"]["amount"]

    assert amount["type"] == "string"
    assert "anyOf" not in amount
    assert amount["examples"] == ["500.00"]


@pytest.mark.parametrize(
    ("method", "path", "statuses"),
    [
        ("get", "/api/accounts/{account_id}", {"404"}),
        ("get", "/api/accounts/{account_id}/balance", {"404"}),
        ("post", "/api/accounts/{account_id}/transactions", {"404", "409"}),
        ("get", "/api/accounts/{account_id}/transactions", {"404"}),
        ("post", "/api/transfers", {"404", "409"}),
    ],
)
def test_error_responses_are_documented(schema, method, path, statuses):
    responses = schema["paths"][path][method]["responses"]

    for code in statuses:
        assert responses[code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
