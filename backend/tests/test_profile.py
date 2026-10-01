import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.profile import DEFAULT_PROFILE, MAX_AVATAR_DATA_URL, PROFILE_KEY, register_profile_routes
from backend.store import Store


def client_for(store):
    app = FastAPI()
    register_profile_routes(app, store)
    return TestClient(app)


@pytest.fixture
def profile(tmp_path):
    store = Store(tmp_path / "data")
    return store, client_for(store)


def avatar(format="PNG", size=(16, 16)):
    stream = BytesIO()
    Image.new("RGB", size, "green").save(stream, format=format)
    mime = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}[format]
    return f"data:image/{mime};base64," + base64.b64encode(stream.getvalue()).decode()


def payload(**changes):
    return {"name": "新的工作台", "bio": "我的脚本库存", "avatar": None, "expected_revision": 0} | changes


def test_defaults_then_full_profile_persists_in_sqlite_and_other_settings_remain(profile):
    store, client = profile
    with store.connection() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('root_catalog', 'retained roots')")
    assert client.get("/api/profile").json() == DEFAULT_PROFILE
    data = payload(name="  工作台姓名  ", bio="  简介  ", avatar=avatar())
    result = client.put("/api/profile", json=data)
    assert result.status_code == 200
    expected = {"name": "工作台姓名", "bio": "简介", "avatar": data["avatar"], "revision": 1}
    assert result.json() == expected
    assert client_for(Store(store.path.parent)).get("/api/profile").json() == expected
    with store.connection() as db:
        assert json.loads(db.execute("SELECT value FROM settings WHERE key=?", (PROFILE_KEY,)).fetchone()[0]) == expected
        assert db.execute("SELECT value FROM settings WHERE key='root_catalog'").fetchone()[0] == "retained roots"


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_supported_real_images_and_removal(profile, format):
    _, client = profile
    image = avatar(format)
    assert client.put("/api/profile", json=payload(avatar=image)).json()["avatar"] == image
    result = client.put("/api/profile", json=payload(avatar=None, expected_revision=1))
    assert result.status_code == 200
    assert result.json()["avatar"] is None


@pytest.mark.parametrize("change", [
    {"name": "  "}, {"name": "x" * 81}, {"bio": "x" * 161}, {"name": 12},
    {"expected_revision": True}, {"expected_revision": -1}, {"unknown": "ignored?"},
    {"avatar": "https://example.test/avatar.png"},
    {"avatar": "data:image/svg+xml;base64,PHN2Zz4="},
    {"avatar": "data:image/png;base64,bm90YW5pbWFnZQ=="},
    {"avatar": "data:image/png;base64,!!!"},
    {"avatar": "data:image/jpeg;base64," + "a" * MAX_AVATAR_DATA_URL},
])
def test_invalid_text_and_avatar_never_change_database(profile, change):
    _, client = profile
    assert client.put("/api/profile", json=payload(**change)).status_code == 422
    assert client.get("/api/profile").json() == DEFAULT_PROFILE


def test_rejects_format_spoofing_truncation_and_excessive_dimensions(profile):
    _, client = profile
    for image in [avatar().replace("image/png", "image/jpeg"), avatar()[:-8], avatar(size=(4097, 1)), avatar(size=(2001, 2000))]:
        assert client.put("/api/profile", json=payload(avatar=image)).status_code == 422
    assert client.get("/api/profile").json() == DEFAULT_PROFILE


def test_revision_prevents_stale_write_and_concurrent_updates(profile):
    store, client = profile
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: client_for(store).put("/api/profile", json=payload(name=name)), ["页面一", "页面二"]))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(response.json() for response in results if response.status_code == 200)
    assert client.get("/api/profile").json() == winner
    assert client.put("/api/profile", json=payload()).status_code == 409
    assert client.get("/api/profile").json() == winner
