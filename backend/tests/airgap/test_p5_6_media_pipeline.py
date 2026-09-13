"""
P5.6 — Sovereign multi-modal pipeline tests.

Coverage groups:

  1. `LocalMediaService.generate_image` — happy path with
     `media.write`, scope denial without, synthetic backend
     produces a valid PNG that lives INSIDE the app's sandbox.

  2. `LocalMediaService.process_vision` — happy path with
     `media.read`, deterministic synthetic transcript keyed on
     image hash, scope denial without, task validation.

  3. HTTP routes — POST /api/apps/{id}/media/generate +
     /media/process round-trip through the same gate stack as
     /fs/read|write; cross-app guard fires when X-App-Id ≠ URL id.

  4. Air-gap invariant — the entire pipeline executes under the
     loopback-only socket kill-switch. No `network_guard.violations`
     are emitted during gen + OCR.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import _reset_for_tests, init_db
from services.app_media import (
    MAX_IMAGE_BYTES,
    MEDIA_SERVICE,
    ImageGenerationResult,
    VisionResult,
    _build_synthetic_png,
)
from services.app_sandbox import (
    SANDBOX_MANAGER,
    _reset_gate_for_tests,
    app_storage_root,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()


def _install(scopes, restrictions=()) -> tuple:
    res = SANDBOX_MANAGER.install(
        {
            "name": "p56-test",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return res["app_id"], res["secret"]


def _build_http() -> FastAPI:
    from api.app_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# (1) Image generation — service-level
# ---------------------------------------------------------------------------


def test_generate_image_succeeds_with_scope(local_db):
    """Test 1 — active app with `media.write` gets a generated image
    path inside its own sandbox."""
    app_id, _ = _install(["media.write", "filesystem.write"])
    result = MEDIA_SERVICE.generate_image(
        app_id,
        "a quiet vOS dashboard",
        resolution=(64, 64),
    )
    assert isinstance(result, ImageGenerationResult)
    assert result.app_id == app_id
    assert result.prompt == "a quiet vOS dashboard"
    assert result.resolution == (64, 64)
    assert result.backend in ("diffusers", "pillow", "synthetic")
    assert result.size_bytes > 0

    # File landed INSIDE the sandbox, not somewhere else.
    on_disk = app_storage_root(app_id) / result.output_path
    assert on_disk.is_file()
    payload = on_disk.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n"), "must be a valid PNG"


def test_generate_image_denied_without_scope(local_db):
    """Test 3 — app WITHOUT `media.write` is blocked with 403."""
    from fastapi import HTTPException

    app_id, _ = _install(["media.read"])  # read-only on media
    with pytest.raises(HTTPException) as exc:
        MEDIA_SERVICE.generate_image(app_id, "anything", resolution=(32, 32))
    assert exc.value.status_code == 403
    assert exc.value.detail["scope"] == "media.write"


def test_generate_image_validates_inputs(local_db):
    app_id, _ = _install(["media.write"])
    with pytest.raises(ValueError, match="prompt"):
        MEDIA_SERVICE.generate_image(app_id, "")
    with pytest.raises(ValueError, match="resolution"):
        MEDIA_SERVICE.generate_image(app_id, "ok", resolution=(0, 0))
    with pytest.raises(ValueError, match="resolution"):
        MEDIA_SERVICE.generate_image(app_id, "ok", resolution=(10_000, 10_000))


def test_synthetic_png_has_well_formed_ihdr_and_iend():
    """The hand-rolled PNG must carry a syntactically valid IHDR
    chunk reporting the requested dimensions, and end with the
    canonical IEND marker. Verified with stdlib `struct` only —
    no Pillow dependency, so the suite stays portable."""
    import struct

    blob = _build_synthetic_png(width=32, height=16, prompt="hello vOS")
    # Magic signature.
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    # IHDR comes immediately after the signature. Length is the
    # next 4 bytes (big-endian); kind is bytes [12:16] == b"IHDR".
    ihdr_len = struct.unpack(">I", blob[8:12])[0]
    assert ihdr_len == 13
    assert blob[12:16] == b"IHDR"
    # IHDR body: width (u32be), height (u32be), bit-depth (u8),
    # color-type (u8), compression (u8), filter (u8), interlace (u8).
    w, h = struct.unpack(">II", blob[16:24])
    assert (w, h) == (32, 16)
    bit_depth, color_type = blob[24], blob[25]
    assert bit_depth == 8 and color_type == 2  # RGB8
    # IEND marker — last 12 bytes are: 0-length + b"IEND" + 4-byte CRC.
    assert blob[-12:-8] == b"\x00\x00\x00\x00"
    assert blob[-8:-4] == b"IEND"


def test_generate_image_path_lands_in_media_generated_subdir(local_db):
    app_id, _ = _install(["media.write"])
    result = MEDIA_SERVICE.generate_image(app_id, "x")
    assert result.output_path.startswith("media/generated/")
    assert result.output_path.endswith(".png")


# ---------------------------------------------------------------------------
# (2) Vision / OCR — service-level
# ---------------------------------------------------------------------------


def test_process_vision_succeeds_with_scope(local_db):
    """Test 2 — active app with `media.read` gets a transcript back."""
    app_id, _ = _install(["media.read"])
    img = _build_synthetic_png(width=32, height=32, prompt="ocr-source")
    result = MEDIA_SERVICE.process_vision(app_id, img, task="ocr")
    assert isinstance(result, VisionResult)
    assert result.task == "ocr"
    assert result.transcript  # non-empty string
    assert 0.0 <= result.confidence <= 1.0
    assert result.backend in ("ollama", "easyocr", "pytesseract", "synthetic")
    # Synthetic path is deterministic — same bytes → same transcript.
    if result.backend == "synthetic":
        again = MEDIA_SERVICE.process_vision(app_id, img, task="ocr")
        assert again.transcript == result.transcript


def test_process_vision_denied_without_scope(local_db):
    from fastapi import HTTPException

    app_id, _ = _install(["media.write"])  # write-only on media
    img = _build_synthetic_png(width=8, height=8, prompt="x")
    with pytest.raises(HTTPException) as exc:
        MEDIA_SERVICE.process_vision(app_id, img, task="ocr")
    assert exc.value.status_code == 403
    assert exc.value.detail["scope"] == "media.read"


def test_process_vision_rejects_oversized_image(local_db):
    """Image larger than the cap is rejected BEFORE any backend call."""
    app_id, _ = _install(["media.read"])
    huge = b"\x89PNG" + b"\x00" * (MAX_IMAGE_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds"):
        MEDIA_SERVICE.process_vision(app_id, huge, task="ocr")


def test_process_vision_validates_task(local_db):
    app_id, _ = _install(["media.read"])
    img = _build_synthetic_png(width=8, height=8, prompt="x")
    with pytest.raises(ValueError, match="task"):
        MEDIA_SERVICE.process_vision(app_id, img, task="hallucinate")


def test_process_vision_synthetic_includes_image_sha256(local_db):
    """Synthetic mode emits a stable transcript keyed on the image
    sha256 so a downstream test can snapshot-compare."""
    app_id, _ = _install(["media.read"])
    img = _build_synthetic_png(width=16, height=16, prompt="snapshot")
    result = MEDIA_SERVICE.process_vision(app_id, img, task="ocr")
    if result.backend == "synthetic":
        assert result.image_sha256[:12] in result.transcript


# ---------------------------------------------------------------------------
# (3) HTTP routes — /media/generate + /media/process
# ---------------------------------------------------------------------------


def test_http_media_generate_happy_path(local_db, network_guard):
    app_id, secret = _install(["media.write"])
    client = TestClient(_build_http())
    r = client.post(
        f"/api/apps/{app_id}/media/generate",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"prompt": "test image", "resolution": [128, 64]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == app_id
    assert body["prompt"] == "test image"
    assert body["resolution"] == [128, 64]
    assert body["size_bytes"] > 0
    assert body["output_path"].startswith("media/generated/")


def test_http_media_generate_403_without_scope(local_db, network_guard):
    app_id, secret = _install(["media.read"])  # no write
    client = TestClient(_build_http())
    r = client.post(
        f"/api/apps/{app_id}/media/generate",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"prompt": "denied"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "media.write"


def test_http_media_process_happy_path(local_db, network_guard):
    app_id, secret = _install(["media.read"])
    img = _build_synthetic_png(width=32, height=32, prompt="http test")
    client = TestClient(_build_http())
    r = client.post(
        f"/api/apps/{app_id}/media/process",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={
            "image_base64": base64.b64encode(img).decode("ascii"),
            "task": "ocr",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == app_id
    assert body["task"] == "ocr"
    assert body["transcript"]
    assert body["image_sha256"]


def test_http_media_process_403_without_scope(local_db, network_guard):
    app_id, secret = _install(["media.write"])  # no read
    img = _build_synthetic_png(width=8, height=8, prompt="x")
    client = TestClient(_build_http())
    r = client.post(
        f"/api/apps/{app_id}/media/process",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"image_base64": base64.b64encode(img).decode("ascii")},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "media.read"


def test_http_media_process_rejects_invalid_base64(local_db, network_guard):
    app_id, secret = _install(["media.read"])
    client = TestClient(_build_http())
    r = client.post(
        f"/api/apps/{app_id}/media/process",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"image_base64": ""},
    )
    assert r.status_code == 400


def test_http_media_cross_app_blocked(local_db, network_guard):
    """App A's secret on App B's URL → 403 BEFORE the service fires."""
    a, a_secret = _install(["media.write"])
    b, _ = _install(["media.write"])
    client = TestClient(_build_http())
    r = client.post(
        f"/api/apps/{b}/media/generate",
        headers={"X-App-Id": a, "X-App-Secret": a_secret},
        json={"prompt": "stolen"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# (4) Air-gap invariant — end-to-end media pipeline is offline
# ---------------------------------------------------------------------------


def test_full_media_cycle_does_not_violate_air_gap(local_db, network_guard):
    """Test 4 — generate + OCR in one run must leave the network
    kill-switch's violations list empty. The synthetic tiers don't
    touch sockets at all; the Ollama tier only ever talks to
    loopback (and is gated by an env-var probe so it doesn't fire
    in the test environment)."""
    app_id, _ = _install(["media.read", "media.write"])
    img_path = MEDIA_SERVICE.generate_image(
        app_id,
        "air-gap snapshot",
        resolution=(64, 64),
    ).output_path
    blob = (app_storage_root(app_id) / img_path).read_bytes()
    transcript = MEDIA_SERVICE.process_vision(
        app_id,
        blob,
        task="describe",
    )
    assert transcript.transcript
    # The fixture asserts the violations list is empty between
    # tests; this assertion captures the in-test state too.
    assert network_guard.violations == []
