"""VOS3 API Client — Python HTTP client for the VOS3 platform."""

from typing import Any, Dict, Optional
import atexit
import json
import threading

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    import urllib.request
except ImportError:
    pass

DEFAULT_BASE_URL = "https://api.vos3.app"

# --- Singleton httpx.Client for connection pooling ---
_client: Optional["httpx.Client"] = None
_client_lock = threading.Lock()


def _get_client() -> "httpx.Client":
    """Lazy-initialize and return a module-level httpx.Client singleton."""
    global _client
    if _client is None:
        with _client_lock:
            # Double-checked locking to avoid race condition.
            if _client is None:
                _client = httpx.Client(timeout=30.0)
                atexit.register(_shutdown_client)
    return _client


def _shutdown_client() -> None:
    """Close the singleton client on interpreter shutdown."""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
        _client = None


class VOS3APIClient:
    """Authenticated API client for VOS3 platform.

    Usage:
        client = VOS3APIClient(app_id="my-app", api_key="...")
        records = client.list_records(entity_id="...")
    """

    def __init__(
        self,
        app_id: str,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        version: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "X-VOS3-App-Id": app_id,
            "Content-Type": "application/json",
        }
        if version:
            self._headers["X-VOS3-App-Version"] = version

    def _request(self, method: str, path: str, body: Optional[Dict] = None) -> Dict:
        url = f"{self.base_url}{path}"
        if _HAS_HTTPX:
            client = _get_client()
            resp = client.request(method, url, headers=self._headers, json=body, timeout=30)
            resp.raise_for_status()
            return resp.json()
        else:
            data = json.dumps(body).encode() if body else None
            req = urllib.request.Request(url, data=data, headers=self._headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status >= 400:
                    raise Exception(f"HTTP {resp.status}: {resp.reason}")
                return json.loads(resp.read())

    # --- Entities ---

    def list_entities(self, org_id: str) -> list:
        return self._request("GET", f"/api/apps/v1/entities?org={org_id}").get("data", [])

    def get_entity(self, entity_id: str) -> Dict:
        return self._request("GET", f"/api/apps/v1/entities/{entity_id}")

    # --- Records ---

    def list_records(self, entity_id: str) -> list:
        return self._request("GET", f"/api/apps/v1/records?entity={entity_id}").get("data", [])

    def create_record(self, entity_id: str, data: Dict) -> Dict:
        return self._request("POST", "/api/apps/v1/records", {"entityId": entity_id, "data": data})

    # --- Workflows ---

    def execute_workflow(self, workflow_id: str, input_data: Optional[Dict] = None) -> Dict:
        return self._request("POST", f"/api/apps/v1/workflows/{workflow_id}/execute", {"input": input_data or {}})

    # --- AI ---

    def generate_text(self, prompt: str, model: Optional[str] = None) -> str:
        result = self._request("POST", "/api/apps/v1/ai/generate", {"prompt": prompt, "model": model})
        return result.get("content", "")

    # --- Files ---

    def read_file(self, path: str) -> str:
        from urllib.parse import quote
        result = self._request("GET", f"/api/apps/v1/files?path={quote(path)}")
        return result.get("content", "")

    def write_file(self, path: str, content: str) -> None:
        self._request("PUT", "/api/apps/v1/files", {"path": path, "content": content})
