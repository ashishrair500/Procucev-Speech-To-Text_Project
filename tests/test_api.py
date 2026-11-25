"""API tests for the CRUD endpoints (run against a temporary SQLite DB)."""

from __future__ import annotations


async def _create(client, **overrides):
    payload = {
        "text": "कल मीटिंग में deadline है, please report भेज देना।",
        "title": "Standup notes",
        "language_hint": "hi-en",
        "duration_seconds": 12,
    }
    payload.update(overrides)
    return await client.post("/api/transcriptions", json=payload)


async def test_create_returns_201_and_row(client):
    res = await _create(client)
    assert res.status_code == 201
    body = res.json()
    assert body["id"] > 0
    assert body["title"] == "Standup notes"
    assert body["source"] == "gemini-live"
    assert body["language_hint"] == "hi-en"
    assert body["duration_seconds"] == 12
    assert body["created_at"] and body["updated_at"]


async def test_create_defaults_title_when_omitted(client):
    res = await _create(client, title=None)
    assert res.status_code == 201
    assert res.json()["title"].startswith("Recording ")


async def test_get_one_and_not_found(client):
    created = (await _create(client)).json()

    ok = await client.get(f"/api/transcriptions/{created['id']}")
    assert ok.status_code == 200
    assert ok.json()["text"] == created["text"]

    missing = await client.get("/api/transcriptions/999999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Transcription not found"


async def test_create_rejects_empty_text(client):
    res = await _create(client, text="   ")
    assert res.status_code == 422


async def test_create_rejects_too_long_text(client):
    res = await _create(client, text="x" * 50_001)
    assert res.status_code == 422


async def test_list_pagination_and_search(client):
    for i in range(7):
        await _create(client, title=f"Note {i}", text=f"body number {i} hindi english")

    first = await client.get("/api/transcriptions?page=1&page_size=3")
    assert first.status_code == 200
    page = first.json()
    assert page["total"] == 7
    assert page["pages"] == 3
    assert len(page["items"]) == 3

    last = await client.get("/api/transcriptions?page=3&page_size=3")
    assert len(last.json()["items"]) == 1

    # Search matches on text and title.
    found = await client.get("/api/transcriptions?search=body number 2")
    assert found.json()["total"] == 1
    assert "body number 2" in found.json()["items"][0]["text"]

    by_title = await client.get("/api/transcriptions?search=note 5")
    assert by_title.json()["total"] == 1

    none = await client.get("/api/transcriptions?search=zzz-no-match")
    assert none.json()["total"] == 0


async def test_update_changes_fields(client):
    created = (await _create(client)).json()

    res = await client.put(
        f"/api/transcriptions/{created['id']}",
        json={"title": "Renamed", "text": "नया text with English words"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Renamed"
    assert body["text"] == "नया text with English words"


async def test_update_not_found(client):
    res = await client.put("/api/transcriptions/424242", json={"title": "x"})
    assert res.status_code == 404


async def test_delete_then_missing(client):
    created = (await _create(client)).json()

    deleted = await client.delete(f"/api/transcriptions/{created['id']}")
    assert deleted.status_code == 204
    assert deleted.content == b""

    assert (await client.get(f"/api/transcriptions/{created['id']}")).status_code == 404
    assert (await client.delete(f"/api/transcriptions/{created['id']}")).status_code == 404


async def test_health_ok(client):
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert "database" in body
    assert isinstance(body["models"], list)
    assert body["models"][0] == "gemini-3.5-transcribe-live"
