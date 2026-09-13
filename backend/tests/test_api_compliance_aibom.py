"""Tests for the Sovereign-Shield external-SPM AI-BOM route.

GET /api/compliance/aibom — read-only, audit/admin-gated. Returns a well-formed
AI-BOM over the registered Sovereign IntegrityCertificates. The corpus is empty
today (no IntegrityCertificate store is wired yet), so the BOM honestly reports
componentCount == 0. The `test_api_` prefix triggers conftest's `_default_api_auth`
fixture (admin auth + CSRF bypass).
"""

from main import app  # noqa: F401  (ensures app import side-effects/conftest auth)


class TestAibomExport:
    def test_returns_200(self, client):
        assert client.get("/api/compliance/aibom").status_code == 200

    def test_is_well_formed_aibom(self, client):
        j = client.get("/api/compliance/aibom").json()
        assert j["@type"] == "AiBillOfMaterials"
        assert j["schemaVersion"] == "ai-spm-2026.1"
        assert "tenant" in j and j["tenant"].get("id")
        assert j["integrityProof"]["alg"] == "SHA-384"

    def test_empty_corpus_is_truthful(self, client):
        # No IntegrityCertificate store is wired yet -> 0 components, honestly.
        j = client.get("/api/compliance/aibom").json()
        assert j["summary"]["componentCount"] == 0
        assert j["components"] == []
