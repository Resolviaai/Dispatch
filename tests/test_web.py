"""Unit test for FastAPI review dashboard."""
from fastapi.testclient import TestClient
from dispatch.web.app import app
from dispatch.db import init_db, save_clip, get_clips_for_review

client = TestClient(app)

def test_web_dashboard():
    init_db()

    # 1. Test homepage
    res = client.get("/")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert "Dispatch" in res.text

    # 2. Test status endpoint
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "disk_free_gb" in data
    assert "ready_clips" in data

    # 3. Create a test clip and verify list endpoint
    clip_id = save_clip(
        session_id=None,
        chunk_id=None,
        start_time=1.0,
        end_time=25.0,
        title="Web test clip title",
        hook="Opening test",
        description="Description",
        hashtags="#Test",
        virality_score=85,
        layout_mode="fit_blur"
    )

    res = client.get("/api/clips")
    assert res.status_code == 200
    clips_data = res.json()
    clip_ids = [c["id"] for c in clips_data["ready"]]
    assert clip_id in clip_ids, f"Clip {clip_id} not found in ready list"

    # 4. Test approval
    res = client.post(f"/api/clips/{clip_id}/approve", json={
        "title": "Approved Title",
        "hashtags": "#Approved",
        "publish_mode": "private",
        "platforms": "youtube,instagram"
    })
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # Verify clip is no longer in ready_review
    res = client.get("/api/clips")
    clip_ids_after = [c["id"] for c in res.json()["ready"]]
    assert clip_id not in clip_ids_after

    print("ALL WEB DASHBOARD TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_web_dashboard()
