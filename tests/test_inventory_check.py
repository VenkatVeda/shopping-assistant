import os

# Must be set before the app or the auth module is imported.
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret")
os.environ["INVENTORY_CHECK_MODE"] = "mock"
os.environ["INVENTORY_MOCK_DELAY_SECONDS"] = "0"

import pytest

from core.inventory_service import STATUSES, InvalidProductId, check_inventory


@pytest.mark.parametrize("status", STATUSES)
def test_mock_returns_the_requested_status(status):
    result = check_inventory("998643070", mock_status=status)
    assert result["status"] == status
    assert result["product_id"] == "998643070"
    assert result["request_id"]


def test_mock_defaults_to_in_stock():
    assert check_inventory("998643070")["status"] == "in_stock"


def test_unknown_carries_a_reason_and_no_timestamp():
    result = check_inventory("998643070", mock_status="unknown")
    assert result["reason"] == "upstream_error"
    assert result["as_of"] is None


@pytest.mark.parametrize("bad_id", [None, "", "a b", "x" * 65, "1; DROP TABLE"])
def test_bad_product_ids_are_rejected(bad_id):
    with pytest.raises(InvalidProductId):
        check_inventory(bad_id)


@pytest.fixture(scope="module")
def client():
    # Importing app.py normally starts the full workflow, which waits on Databricks for minutes.
    # The inventory route does not use it, so make that start-up fail fast; app.py then sets workflow = None.
    import core

    def _skip_workflow(*args, **kwargs):
        raise RuntimeError("workflow disabled in unit tests")

    core.ShoppingAssistantWorkflow = _skip_workflow
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def auth_headers():
    from core.auth import generate_access_token
    return {"Authorization": f"Bearer {generate_access_token('u1', 'tester@example.com', 'Tester')}"}


def test_route_requires_login(client):
    assert client.post("/api/inventory/check", json={"product_id": "998643070"}).status_code == 401


def test_route_returns_the_status(client, auth_headers):
    resp = client.post("/api/inventory/check", json={"product_id": "998643070", "mock_status": "out_of_stock"},
                       headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "out_of_stock"


def test_route_rejects_a_bad_id(client, auth_headers):
    resp = client.post("/api/inventory/check", json={"product_id": "bad id"}, headers=auth_headers)
    assert resp.status_code == 400
