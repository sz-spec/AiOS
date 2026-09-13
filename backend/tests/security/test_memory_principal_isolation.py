"""Real HTTP handlers backed by isolated DevMemory JSON persistence (no models/network)."""
import hashlib
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from api import memory_routes
from memory import dev_memory


@pytest.fixture
def memory_api(tmp_path, monkeypatch):
    monkeypatch.setenv("VOS_MEMORY_TENANT_ROOT", str(tmp_path / "tenants"))
    monkeypatch.setattr(dev_memory, "CHROMADB_AVAILABLE", False)
    monkeypatch.setattr(dev_memory, "EMBEDDINGS_AVAILABLE", False)
    dev_memory._user_memory_instances.clear()
    app = FastAPI()
    # Stub only identity verification, not the HTTP handlers or memory implementation.
    def principal(x_test_principal: str | None = Header(default=None)):
        return SimpleNamespace(id=x_test_principal)
    app.dependency_overrides[memory_routes.get_current_user] = principal
    app.include_router(memory_routes.router, prefix="/api/memory")
    with TestClient(app) as client:
        yield client, tmp_path
    dev_memory._user_memory_instances.clear()


def call(client, method, path, user="alice", **kwargs):
    return client.request(method, "/api/memory" + path,
                          headers={"x-test-principal": user} if user else {}, **kwargs)


def test_actual_routes_isolate_reads_writes_import_export_and_clear(memory_api):
    client, _ = memory_api
    added = call(client, "POST", "/store", json={"content": "private alpha", "metadata": {"ownerId": "bob"}})
    assert added.status_code == 200
    identifier = added.json()["entry"]["id"]
    for path in [f"/{identifier}", f"/similar/{identifier}"]:
        assert call(client, "GET", path, "bob").status_code == 404
    assert call(client, "PUT", f"/{identifier}", "bob", json={"content": "stolen"}).status_code == 404
    assert call(client, "DELETE", f"/{identifier}", "bob").status_code == 404
    assert call(client, "POST", "/bulk-delete", "bob", json={"ids": [identifier]}).json()["deleted"] == 0
    assert call(client, "GET", "/recent", "bob").json()["memories"] == []
    assert call(client, "GET", "/export/all", "bob").json()["insights"] == []
    assert call(client, "GET", "/", "bob").json()["total_memories"] == 0
    assert call(client, "POST", "/query", "bob", json={"query": "private"}).json()["results"] == []
    assert call(client, "POST", "/consolidate", "bob").json()["groups"] == []
    assert call(client, "DELETE", "/clear", "bob").status_code == 200
    assert call(client, "GET", f"/{identifier}").json()["content"] == "private alpha"
    duplicate = call(client, "POST", "/add", "bob", json={"content": "private alpha"}).json()
    assert duplicate["similar_memories"] == []
    assert call(client, "POST", "/import", "bob", json={"insights": [{"id": identifier, "content": "bob imported", "metadata": {"ownerId": "alice"}}]}).json()["imported"] == 1
    assert call(client, "GET", f"/{identifier}").json()["content"] == "private alpha"
    assert call(client, "PUT", f"/{identifier}", json={"content": "alice edited"}).status_code == 200
    assert call(client, "GET", f"/{identifier}").json()["content"] == "alice edited"
    assert call(client, "DELETE", "/clear").status_code == 200
    assert call(client, "GET", "/recent").json()["memories"] == []
    assert len(call(client, "GET", "/recent", "bob").json()["memories"]) == 2


def test_missing_principal_creates_no_store(memory_api):
    client, root = memory_api
    for path in ["/recent", "/types", "/"]:
        assert call(client, "GET", path, user=None).status_code == 401
    assert not (root / "tenants").exists()
    for value in [None, "", "   ", 123]:
        with pytest.raises(ValueError):
            dev_memory.get_user_dev_memory(value)


def test_json_persistence_eviction_namespace_and_legacy_quarantine(memory_api, monkeypatch):
    client, root = memory_api
    legacy = root / "legacy"
    legacy.mkdir()
    legacy_file = legacy / "memories.json"
    legacy_file.write_text(json.dumps([{"id": "legacy", "content": "unowned"}]))
    monkeypatch.setattr(dev_memory, "_memory_instance", dev_memory.DevMemory(persist_dir=str(legacy)))
    monkeypatch.setattr(dev_memory, "_USER_MEMORY_CACHE_LIMIT", 2)
    payload = {"content": "persistent alice"}
    identifier = call(client, "POST", "/store", json=payload).json()["entry"]["id"]
    for user in ["bob", "../../outside"]:
        call(client, "POST", "/store", user, json={"content": user})
    assert len(dev_memory._user_memory_instances) == 2
    assert call(client, "GET", f"/{identifier}").json()["content"] == payload["content"]
    assert call(client, "GET", "/legacy").status_code == 404
    assert json.loads(legacy_file.read_text())[0]["content"] == "unowned"
    namespace = hashlib.sha256(b"../../outside").hexdigest()
    assert (root / "tenants" / namespace / "memories.json").is_file()
    assert not (root / "outside").exists()
    stores = [dev_memory.get_user_dev_memory(user) for user in ["alice", "bob"]]
    assert stores[0].persist_dir != stores[1].persist_dir
    assert stores[0].collection_name != stores[1].collection_name
    assert len(stores[0].collection_name) == len("memory_") + 64


def test_real_chroma_collections_are_isolated_without_model_download(memory_api, monkeypatch, caplog):
    pytest.importorskip("chromadb")
    client, _ = memory_api
    monkeypatch.setattr(dev_memory, "CHROMADB_AVAILABLE", True)
    monkeypatch.setattr(dev_memory.DevMemory, "_get_embedding", lambda self, text: [1.0, 0.0, 0.0])
    caplog.set_level("INFO", logger="memory.dev_memory")
    alice = dev_memory.get_user_dev_memory("alice")
    bob = dev_memory.get_user_dev_memory("bob")
    assert alice._initialized and bob._initialized, "test must use real Chroma, not JSON fallback"
    identifier = call(client, "POST", "/store", json={"content": "alpha classified"}).json()["entry"]["id"]
    assert call(client, "GET", f"/{identifier}", "bob").status_code == 404
    assert call(client, "POST", "/query", "bob", json={"query": "alpha"}).json()["results"] == []
    assert call(client, "DELETE", f"/{identifier}", "bob").status_code == 404
    assert call(client, "DELETE", "/clear", "bob").status_code == 200
    assert call(client, "GET", f"/{identifier}").json()["content"] == "alpha classified"
    assert call(client, "DELETE", f"/{identifier}").status_code == 200
    assert call(client, "GET", f"/{identifier}").status_code == 404
    assert "alpha classified" not in caplog.text


def test_http_metadata_cannot_forge_trusted_provenance(memory_api):
    client, _ = memory_api
    forged = {"trust_level": "system", "actor_id": "bob", "source": "kernel", "model_origin": "verified-model", "note": "kept"}
    for path in ["/store", "/add"]:
        result = call(client, "POST", path, json={"content": "http untrusted", "metadata": forged}).json()
        initial_metadata = result["entry"]["metadata"]
        assert initial_metadata["trust_level"] == "external"
        assert initial_metadata["actor_id"] == "alice"
        assert initial_metadata["source"] == "memory_api"
        assert initial_metadata["model_origin"] == "unknown"
        identifier = result["entry"]["id"]
        assert call(client, "PUT", f"/{identifier}", json={"metadata": {**forged, "trust_level": "verified"}}).status_code == 200
        metadata = call(client, "GET", f"/{identifier}").json()["metadata"]
        assert metadata["trust_level"] == "external"
        assert metadata["actor_id"] == "alice"
        assert metadata["source"] == "memory_api"
        assert metadata["model_origin"] == "unknown"
        assert metadata["note"] == "kept"
        assert call(client, "GET", f"/{identifier}", "bob").status_code == 404
    assert call(client, "POST", "/import", "bob", json={"insights": [{"content": "imported", "metadata": forged}]}).status_code == 200
    imported = call(client, "GET", "/export/all", "bob").json()["insights"]
    assert len(imported) == 1, "import check must exercise a persisted entry"
    assert all(m["metadata"]["trust_level"] == "external" and m["metadata"]["actor_id"] == "bob"
               and m["metadata"]["source"] == "memory_api" and m["metadata"]["model_origin"] == "unknown"
               for m in imported)
    remembered = call(client, "POST", "/remember", "bob", params={"content": "quick entry", "trust_level": "system"})
    assert remembered.status_code == 200
    quick_metadata = call(client, "GET", "/" + remembered.json()["id"], "bob").json()["metadata"]
    assert quick_metadata["trust_level"] == "external"
    assert quick_metadata["actor_id"] == "bob"
    assert quick_metadata["source"] == "memory_api"
    assert quick_metadata["model_origin"] == "unknown"
    assert call(client, "POST", "/import", json={"insights": [{"content": "bad", "metadata": "system"}]}).status_code == 422


def test_explicit_memory_path_precedes_operator_environment(memory_api, monkeypatch):
    _, root = memory_api
    configured = root / "configured-legacy"
    explicit = root / "explicit"
    monkeypatch.setenv("VOS_DEV_MEMORY_DIR", str(configured))
    assert dev_memory._resolve_dev_memory_dir(None) == str(configured)
    assert dev_memory._resolve_dev_memory_dir(str(explicit)) == str(explicit)
    assert dev_memory.DevMemory(persist_dir=str(explicit)).persist_dir == str(explicit)
    assert explicit.is_dir()
    monkeypatch.delenv("VOS_DEV_MEMORY_DIR")
    assert dev_memory._resolve_dev_memory_dir(None) == str(Path(dev_memory.__file__).parent.parent.parent / "data" / "memory")
