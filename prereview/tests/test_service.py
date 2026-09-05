"""Conformance по чек-листу ai-handoff.md с заглушкой модели."""

import json
import uuid

from fastapi.testclient import TestClient

from conftest import criteria_payload
from prereview.artifact.fetch import digest_of


def make_client():
    from prereview.service.app import app

    return TestClient(app)


def assist_request(md_artifact, criteria=None, fingerprint=None, attempt=1, run_id=None):
    data = md_artifact.read_bytes()
    return {
        "contract_version": "2.0.0", "purpose": "reviewer_assist", "run_id": run_id or str(uuid.uuid4()),
        "attempt": attempt, "input_fingerprint": fingerprint or "sha256:" + "a" * 64,
        "review_iteration_id": str(uuid.uuid4()), "artifact_id": str(uuid.uuid4()),
        "artifact_url": f"file://{md_artifact}", "artifact_digest": digest_of(data), "media_type": "text/markdown",
        "student_text": "Сделайте лабу", "criteria": criteria or criteria_payload(), "reviewer_guidance": "секретные указания",
        "reference_url": None,
    }


def test_assist_running_then_succeeded(settings, md_artifact):
    client = make_client()
    req = assist_request(md_artifact)
    r = client.post(f"/v2/review-assists/{req['run_id']}", json=req, headers={"Idempotency-Key": f"{req['run_id']}:1"})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["status"] in {"running", "succeeded"} and first["input_fingerprint"] == req["input_fingerprint"]
    g = client.get(f"/v2/review-assists/{req['run_id']}", params={"attempt": 1})
    ev = g.json()
    assert ev["status"] == "succeeded" and ev["sequence"] >= first["sequence"]
    ids = [s["criterion_id"] for s in ev["result"]["suggestions"]]
    assert sorted(ids) == sorted(c["id"] for c in req["criteria"])
    assert ev["result"]["authorship_signal"]["probability"] is None
    # повтор POST не создаёт вторую работу и отдаёт то же финальное событие
    r2 = client.post(f"/v2/review-assists/{req['run_id']}", json=req)
    assert r2.json()["event_id"] == ev["event_id"]
    runs = list((settings.data_dir / "runs").glob("*.json"))
    assert len(runs) == 1
    # чужой fingerprint для той же пары run/attempt не принимается
    bad = dict(req, input_fingerprint="sha256:" + "b" * 64)
    assert client.post(f"/v2/review-assists/{req['run_id']}", json=bad).status_code == 409


def test_lookup_unknown_is_404(settings):
    client = make_client()
    assert client.get(f"/v2/review-assists/{uuid.uuid4()}", params={"attempt": 1}).status_code == 404


def test_recovery_after_restart(settings, md_artifact):
    client = make_client()
    req = assist_request(md_artifact)
    client.post(f"/v2/review-assists/{req['run_id']}", json=req)
    # «рестарт»: новое приложение над тем же каталогом данных
    client2 = make_client()
    ev = client2.get(f"/v2/review-assists/{req['run_id']}", params={"attempt": 1}).json()
    assert ev["status"] == "succeeded"


def test_unsupported_format(settings, md_artifact):
    client = make_client()
    req = assist_request(md_artifact)
    req["media_type"] = "application/x-tar"
    client.post(f"/v2/review-assists/{req['run_id']}", json=req)
    ev = client.get(f"/v2/review-assists/{req['run_id']}", params={"attempt": 1}).json()
    assert ev["status"] == "failed" and ev["error_code"] == "unsupported_format" and ev["result"] is None


def test_bad_digest_is_invalid_artifact(settings, md_artifact):
    client = make_client()
    req = assist_request(md_artifact)
    req["artifact_digest"] = "sha256:" + "c" * 64
    r = client.post(f"/v2/review-assists/{req['run_id']}", json=req)
    assert r.json()["status"] == "failed" and r.json()["error_code"] == "invalid_artifact"


def test_self_review_public_only(settings, md_artifact):
    client = make_client()
    data = md_artifact.read_bytes()
    req = {
        "contract_version": "2.0.0", "purpose": "student_self_review", "run_id": str(uuid.uuid4()), "attempt": 1,
        "input_fingerprint": "sha256:" + "d" * 64, "artifact_id": str(uuid.uuid4()),
        "artifact_url": f"file://{md_artifact}", "artifact_digest": digest_of(data), "media_type": "text/markdown",
        "student_text": "Сделайте лабу", "criteria": criteria_payload(3, private=False),
    }
    client.post(f"/v2/self-reviews/{req['run_id']}", json=req)
    ev = client.get(f"/v2/self-reviews/{req['run_id']}", params={"attempt": 1}).json()
    assert ev["status"] == "succeeded"
    findings = ev["result"]["findings"]
    assert len(findings) == 3 and all(f["status"] in {"met", "needs_attention", "not_checked"} for f in findings)
    dumped = json.dumps(ev, ensure_ascii=False)
    assert "proposed_points" not in dumped and "reviewer_note" not in dumped


def test_bearer_required_when_configured(settings, md_artifact, monkeypatch):
    from prereview.config import get_settings

    monkeypatch.setenv("PREREVIEW_TOKEN", "secret-token")
    get_settings.cache_clear()
    client = make_client()
    req = assist_request(md_artifact)
    assert client.post(f"/v2/review-assists/{req['run_id']}", json=req).status_code == 401
    ok = client.post(f"/v2/review-assists/{req['run_id']}", json=req, headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200


def test_empty_token_means_no_auth(settings, md_artifact, monkeypatch):
    from prereview.config import get_settings

    monkeypatch.setenv("PREREVIEW_TOKEN", "")
    get_settings.cache_clear()
    assert get_settings().token is None
    client = make_client()
    req = assist_request(md_artifact)
    assert client.post(f"/v2/review-assists/{req['run_id']}", json=req).status_code == 200
