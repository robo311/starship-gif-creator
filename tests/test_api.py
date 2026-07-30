"""HTTP behaviour, including the Range support that video seeking depends on."""

import time

from .conftest import SYNTHETIC_ID

SPEC = {
    "video_id": SYNTHETIC_ID,
    "start": 0.5,
    "duration": 1.0,
    "fps": 10,
    "width": 160,
    "height": 120,
    "preset": "balanced",
}


def await_job(client, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/job/{job_id}").json()
        if job["state"] == "done":
            return job["result"]
        if job["state"] == "error":
            raise AssertionError(f"job failed: {job['error']}")
        time.sleep(0.15)
    raise AssertionError("job did not finish in time")


def test_health_reports_the_tools_and_the_size_model(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True, f"missing tools: {body['missing']}"
    assert set(body["presets"]) == {"max", "balanced", "small", "tiny"}
    assert body["model"]["headerBytes"] > 0
    assert "bayer" in body["model"]["ditherBpp"]


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Starship" in response.text
    assert "application/ld+json" in response.text


def test_brand_and_discovery_assets_are_served(client):
    manifest = client.get("/site.webmanifest")
    assert manifest.status_code == 200
    assert manifest.json()["short_name"] == "Starship"

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Allow: /" in robots.text

    icon = client.get("/assets/starship-icon.svg")
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")

    grain = client.get("/assets/grain.svg")
    assert grain.status_code == 200
    assert grain.headers["content-type"].startswith("image/svg+xml")


def test_the_ui_assets_are_never_served_from_a_guess(client):
    """An edited script must reach the browser on reload, not sit in its cache."""
    for path in ("/js/main.js", "/css/app.css"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path


def test_estimate_returns_bytes_and_frames(client):
    body = client.post("/api/estimate", json={"spec": SPEC}).json()
    assert body["frames"] == 10
    assert body["bytes"] > 0
    assert body["calibrated"] is False


def test_estimate_grows_with_resolution(client):
    small = client.post("/api/estimate", json={"spec": {**SPEC, "width": 160, "height": 120}}).json()
    large = client.post("/api/estimate", json={"spec": {**SPEC, "width": 640, "height": 480}}).json()
    assert large["bytes"] > small["bytes"] * 10


def test_unknown_video_cannot_be_rendered(client):
    response = client.post("/api/render", json={**SPEC, "video_id": "missingvid"})
    assert response.status_code == 404


def test_malformed_video_id_is_rejected(client):
    assert client.get("/api/video/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)


def test_invalid_spec_is_rejected(client):
    response = client.post("/api/render", json={**SPEC, "fps": 999})
    assert response.status_code == 422


def test_video_is_served_whole_and_advertises_range_support(client):
    response = client.get(f"/api/video/{SYNTHETIC_ID}")
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert len(response.content) > 1000


def test_source_video_can_be_downloaded_with_a_friendly_filename(client):
    response = client.get(
        f"/api/video/{SYNTHETIC_ID}",
        params={"download": 1, "filename": "my-favourite-video"},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="my-favourite-video.mp4"'
    assert response.headers["content-type"] == "video/mp4"
    assert len(response.content) > 1000


def test_source_video_download_rejects_a_hostile_filename(client):
    response = client.get(
        f"/api/video/{SYNTHETIC_ID}",
        params={"download": 1, "filename": "../../not-my-video.mp4"},
    )
    assert response.headers["content-disposition"] == f'attachment; filename="{SYNTHETIC_ID}.mp4"'


def test_range_request_returns_exactly_the_requested_slice(client):
    whole = client.get(f"/api/video/{SYNTHETIC_ID}").content
    response = client.get(f"/api/video/{SYNTHETIC_ID}", headers={"Range": "bytes=10-109"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 10-109/{len(whole)}"
    assert response.content == whole[10:110]


def test_open_ended_range_runs_to_the_last_byte(client):
    whole = client.get(f"/api/video/{SYNTHETIC_ID}").content
    response = client.get(f"/api/video/{SYNTHETIC_ID}", headers={"Range": "bytes=50-"})
    assert response.status_code == 206
    assert response.content == whole[50:]


def test_suffix_range_returns_the_tail(client):
    whole = client.get(f"/api/video/{SYNTHETIC_ID}").content
    response = client.get(f"/api/video/{SYNTHETIC_ID}", headers={"Range": "bytes=-64"})
    assert response.status_code == 206
    assert response.content == whole[-64:]


def test_range_beyond_the_file_is_clamped(client):
    whole = client.get(f"/api/video/{SYNTHETIC_ID}").content
    response = client.get(f"/api/video/{SYNTHETIC_ID}", headers={"Range": f"bytes=0-{len(whole) + 5000}"})
    assert response.status_code == 206
    assert len(response.content) == len(whole)


def test_render_end_to_end_then_serve_and_download_the_gif(client):
    job_id = client.post("/api/render", json=SPEC).json()["job_id"]
    result = await_job(client, job_id)

    assert result["bytes"] > 0
    assert (result["width"], result["height"]) == (160, 120)
    assert result["frames"] >= 9

    served = client.get(result["gif_url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/gif"
    assert served.content[:6] in (b"GIF89a", b"GIF87a")
    assert len(served.content) == result["bytes"]

    download = client.get(result["gif_url"], params={"download": 1})
    assert "attachment" in download.headers["content-disposition"]


def test_download_honours_a_friendly_filename(client):
    """Content-Disposition beats the anchor's `download` attribute, so it must obey."""
    job_id = client.post("/api/render", json=SPEC).json()["job_id"]
    result = await_job(client, job_id)

    response = client.get(result["gif_url"], params={"download": 1, "filename": "my-clip-12.5s.gif"})
    assert 'filename="my-clip-12.5s.gif"' in response.headers["content-disposition"]


def test_a_hostile_download_filename_falls_back_to_the_cache_name(client):
    job_id = client.post("/api/render", json=SPEC).json()["job_id"]
    result = await_job(client, job_id)

    for hostile in ('../../etc/passwd', 'no-extension', 'quote".gif', 'a/b.gif'):
        response = client.get(result["gif_url"], params={"download": 1, "filename": hostile})
        disposition = response.headers["content-disposition"]
        assert f'filename="{result["name"]}"' == disposition.split("; ", 1)[1], hostile


def test_a_render_calibrates_later_estimates(client):
    """The point of calibration: the estimate must learn from a real render."""
    from server import app as app_module

    # Earlier tests in this session may already have rendered this clip.
    app_module._calibration.clear()

    before = client.post("/api/estimate", json={"spec": SPEC}).json()
    assert before["calibrated"] is False

    job_id = client.post("/api/render", json=SPEC).json()["job_id"]
    result = await_job(client, job_id)

    after = client.post("/api/estimate", json={"spec": SPEC}).json()
    assert after["calibrated"] is True

    error_before = abs(before["bytes"] - result["bytes"]) / result["bytes"]
    error_after = abs(after["bytes"] - result["bytes"]) / result["bytes"]
    assert error_after <= error_before
    assert error_after < 0.10, f"calibrated estimate off by {error_after:.1%}"


def test_fit_reaches_a_demanding_target_on_real_footage(client):
    target = 60_000
    spec = {**SPEC, "width": 320, "height": 240, "fps": 15, "duration": 1.5, "preset": "max"}
    job_id = client.post("/api/fit", json={"spec": spec, "target_bytes": target}).json()["job_id"]
    result = await_job(client, job_id, timeout=300)

    assert result["met"] is True, result["notes"]
    assert result["bytes"] <= target
    assert result["renders"] >= 1
    assert client.get(result["gif_url"]).status_code == 200


def test_fit_returns_the_settings_it_settled_on(client):
    spec = {**SPEC, "duration": 1.0, "preset": "max"}
    job_id = client.post("/api/fit", json={"spec": spec, "target_bytes": 40_000}).json()["job_id"]
    result = await_job(client, job_id, timeout=300)

    assert result["spec"]["video_id"] == SYNTHETIC_ID
    assert result["spec"]["width"] % 2 == 0
    assert result["target_bytes"] == 40_000


def test_fit_respects_its_render_budget(client):
    spec = {**SPEC, "duration": 1.0}
    body = {"spec": spec, "target_bytes": 10_000, "max_renders": 2}
    job_id = client.post("/api/fit", json=body).json()["job_id"]
    result = await_job(client, job_id, timeout=300)
    assert result["renders"] <= 2


def test_fit_rejects_a_nonsensical_target(client):
    assert client.post("/api/fit", json={"spec": SPEC, "target_bytes": 5}).status_code == 422


def test_render_accepts_the_effect_options(client):
    spec = {**SPEC, "duration": 1.0, "speed": 1.5, "boomerang": True,
            "sharpen": 0.8, "loop_forever": False}
    job_id = client.post("/api/render", json=spec).json()["job_id"]
    result = await_job(client, job_id)
    assert result["bytes"] > 0
    assert result["frames"] > 1


def test_out_of_range_effect_values_are_rejected(client):
    for bad in ({"speed": 99}, {"sharpen": 9}, {"speed": 0.01}):
        assert client.post("/api/render", json={**SPEC, **bad}).status_code == 422


def test_missing_gif_is_reported_cleanly(client):
    assert client.get("/api/gif/nosuchthing.gif").status_code == 404


def test_gif_name_traversal_is_rejected(client):
    assert client.get("/api/gif/..%2F..%2Fsecret.gif").status_code in (400, 404)


def test_unknown_job_is_a_404(client):
    assert client.get("/api/job/deadbeef").status_code == 404


def test_load_rejects_an_empty_url(client):
    assert client.post("/api/load", json={"url": "   "}).status_code == 400
