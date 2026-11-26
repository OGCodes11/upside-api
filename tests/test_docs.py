"""The /docs page: an introduction, grouped endpoints with help text, and no raw endpoints in public mode."""

import json
import re

import pytest

PUBLIC_PATHS = {"/v1/prospects", "/v1/prospects/{prospect_id}/psi", "/v1/players/{player_id}/progression"}
RAW_PATHS = {"/v1/players", "/v1/players/{player_id}", "/v1/players/{player_id}/seasons"}
RAW_MODELS = {"PlayerSeason", "SeasonLine", "PlayerProfile", "Page_PlayerSeason_", "Page_SeasonLine_"}


@pytest.fixture
def public_mode(monkeypatch):
    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.setenv("UPSIDE_API_KEY", "test-key")


@pytest.fixture
def local_mode(monkeypatch):
    monkeypatch.delenv("PUBLIC_MODE", raising=False)


def test_intro_states_the_backtest_result(client, local_mode):
    info = client.get("/openapi.json").json()["info"]
    assert "0.15–0.18" in info["description"] and "0.58–0.62" in info["description"]
    assert "Derived data only" in info["description"]


def test_local_docs_list_every_endpoint(client, local_mode):
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == PUBLIC_PATHS | RAW_PATHS
    assert [t["name"] for t in schema["tags"]] == ["Prospects", "Players", "Raw data (local only)"]
    assert "securitySchemes" in schema["components"]


def test_public_docs_hide_the_raw_endpoints(client, public_mode):
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == PUBLIC_PATHS
    assert [t["name"] for t in schema["tags"]] == ["Prospects", "Players"]
    assert "securitySchemes" not in schema["components"]
    assert not RAW_MODELS & set(schema["components"]["schemas"])


def test_public_docs_have_no_broken_references(client, public_mode):
    schema = client.get("/openapi.json").json()
    refs = set(re.findall(r"#/components/schemas/([\w-]+)", json.dumps(schema)))
    assert refs <= set(schema["components"]["schemas"])


def test_hidden_endpoints_stay_locked(client, public_mode):
    assert client.get("/v1/players").status_code == 401


def test_every_public_endpoint_is_explained(client, public_mode):
    for path, ops in client.get("/openapi.json").json()["paths"].items():
        op = ops["get"]
        assert op["tags"] and op["summary"] and op["description"], path
        for param in op.get("parameters", []):
            assert param.get("description"), (path, param["name"])


def test_docs_page_opens_ready_to_try(client):
    page = client.get("/docs").text
    assert '"tryItOutEnabled": true' in page
    assert '"defaultModelsExpandDepth": -1' in page
