"""End-to-end tests over the HTTP surface, using mock providers only."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from andon.config import get_settings

BENGALURU = {"lat": 12.9716, "lon": 77.5946, "name": "Indiranagar Kitchen"}


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    # Mock everything: tests must not depend on a network or on credentials.
    monkeypatch.setenv("ANDON_WEATHER_PROVIDERS", "mock")
    monkeypatch.setenv("ANDON_TRAFFIC_PROVIDERS", "mock")
    monkeypatch.setenv("ANDON_INCIDENT_PROVIDERS", "mock")
    monkeypatch.setenv("ANDON_RADAR_PROVIDERS", "none")
    monkeypatch.setenv("ANDON_ACCESS_GRAPH_SOURCE", "mock")
    monkeypatch.setenv("ANDON_DATABASE_PATH", str(tmp_path / "andon.sqlite3"))
    monkeypatch.setenv("ANDON_LLM_ENABLED", "false")
    monkeypatch.setenv("ANDON_MOCK_SCENARIO", "storm")
    monkeypatch.setenv("ANDON_LOG_JSON", "false")
    monkeypatch.delenv("ANDON_HISTORY_FILE", raising=False)
    get_settings.cache_clear()

    from andon.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_status_reports_mock_providers_and_disabled_llm(client):
    body = client.get("/api/status").json()

    assert [w["mode"] for w in body["providers"]["weather"]] == ["mock"]
    assert [t["mode"] for t in body["providers"]["traffic"]] == ["mock"]
    assert body["llm"]["available"] is False
    assert body["mock_scenario"] == "storm"


class TestSituationEndpoint:
    def test_returns_a_complete_report(self, client):
        body = client.get("/api/situation", params=BENGALURU).json()

        situation = body["situation"]
        assert situation["restaurant"]["name"] == "Indiranagar Kitchen"
        assert situation["overall"]["level"] in {"none", "low", "medium", "high", "severe"}
        assert 0 <= situation["overall"]["score"] <= 100
        assert situation["fingerprint"]

        for signal in ("weather", "traffic", "road_conditions"):
            assert situation[signal]["status"] in {"ok", "degraded", "unavailable"}

        report = body["report"]
        assert report["generator"] == "rules-fallback"
        assert report["summary"]
        assert report["outlook_30_60min"]
        assert len(body["sources"]) == 3
        assert body["trends"]

    def test_every_source_carries_provenance(self, client):
        body = client.get("/api/situation", params=BENGALURU).json()

        for entry in body["sources"]:
            assert entry["source"]["id"]
            assert entry["source"]["mode"] in {"live", "mock"}
            assert entry["freshness"] in {"fresh", "recent", "aging", "stale", "unknown"}
            assert entry["fetched_at"]

    def test_every_evidence_item_is_typed_and_sourced(self, client):
        body = client.get("/api/situation", params=BENGALURU).json()

        items = [
            item
            for signal in ("weather", "traffic", "road_conditions")
            for item in body["situation"][signal]["evidence"]
        ]
        assert items
        for item in items:
            assert item["kind"] in {"observed", "forecast", "inferred"}
            assert item["source_name"]
            assert item["confidence"] in {"low", "medium", "high"}

    def test_simulated_sources_are_declared_in_the_report(self, client):
        body = client.get("/api/situation", params=BENGALURU).json()
        assert any("simulated" in u.lower() for u in body["report"]["uncertainties"])

    def test_second_call_reuses_the_report_when_nothing_changed(self, client):
        first = client.get("/api/situation", params=BENGALURU).json()
        second = client.get("/api/situation", params=BENGALURU).json()

        assert first["report"]["reused"] is False
        # Same fingerprint -> no new report, and the reason says why.
        if second["situation"]["fingerprint"] == first["situation"]["fingerprint"]:
            assert second["report"]["reused"] is True
            assert "unchanged" in second["report"]["reuse_reason"]

    def test_history_accumulates_and_is_returned(self, client):
        client.get("/api/situation", params=BENGALURU)
        body = client.get("/api/situation", params=BENGALURU).json()

        assert len(body["history"]) >= 1
        assert body["history"][-1]["level"] == body["situation"]["overall"]["level"]

    def test_separate_locations_do_not_share_history(self, client):
        client.get("/api/situation", params=BENGALURU)
        other = client.get(
            "/api/situation", params={"lat": 19.076, "lon": 72.8777, "name": "Bandra"}
        ).json()

        assert len(other["history"]) == 1


class TestPollFlag:
    """poll=false must not touch a provider — a page load, not a refresh."""

    def _checked_ats(self, body) -> set[str]:
        return {h["checked_at"] for h in body["evidence"]["source_health"]}

    def test_poll_false_never_contacts_a_provider(self, client):
        first = client.get("/api/situation", params=BENGALURU).json()
        before = self._checked_ats(first)

        # Two more page-load-style requests: no source_health record may move,
        # because that timestamp is only written inside evidence.refresh().
        for _ in range(2):
            again = client.get("/api/situation", params={**BENGALURU, "poll": False}).json()
            assert self._checked_ats(again) == before
            assert again["situation"]["generated_at"] == first["situation"]["generated_at"]
            assert again["restaurant_id"] == first["restaurant_id"]

    def test_poll_false_bootstraps_when_nothing_has_ever_been_polled(self, client):
        # A brand-new location with no prior poll still has to return something.
        body = client.get("/api/situation", params={**BENGALURU, "poll": False}).json()
        assert body["situation"]["overall"]["level"] in {"none", "low", "medium", "high", "severe"}
        assert body["evidence"]["source_health"]

    def test_force_after_poll_false_still_gets_a_real_poll(self, client):
        client.get("/api/situation", params={**BENGALURU, "poll": False})
        forced = client.get("/api/situation", params={**BENGALURU, "force": True}).json()
        again = client.get("/api/situation", params={**BENGALURU, "poll": False}).json()
        # The cached response served by poll=false is now the forced one.
        assert again["situation"]["generated_at"] == forced["situation"]["generated_at"]

    def test_poll_defaults_to_true_so_existing_callers_are_unaffected(self, client):
        first = client.get("/api/situation", params=BENGALURU).json()
        second = client.get("/api/situation", params=BENGALURU).json()
        # Both are real polls (default poll=True); each writes its own checked_at.
        assert self._checked_ats(first) and self._checked_ats(second)

    @pytest.mark.parametrize(
        "params",
        [
            {"lat": 91, "lon": 0},
            {"lat": 0, "lon": 181},
            {"lat": "abc", "lon": 0},
            {"lon": 77.5},
        ],
    )
    def test_invalid_coordinates_are_rejected(self, client, params):
        assert client.get("/api/situation", params=params).status_code == 422


class TestHistoryEndpoint:
    def test_empty_for_an_unseen_location(self, client):
        body = client.get("/api/history", params={"lat": 1.0, "lon": 1.0}).json()

        assert body["snapshots"] == []
        assert body["direction"] == "unknown"

    def test_returns_snapshots_after_a_situation_call(self, client):
        client.get("/api/situation", params=BENGALURU)
        body = client.get(
            "/api/history", params={"lat": BENGALURU["lat"], "lon": BENGALURU["lon"]}
        ).json()

        assert len(body["snapshots"]) == 1
        assert body["snapshots"][0]["level"]
        assert body["window_minutes"] == 180


class TestStaticServing:
    """The SPA catch-all must not swallow API 404s — an HTML body with a 200
    turns a typo'd endpoint into an unexplained JSON parse error client-side."""

    def test_unknown_api_path_is_a_json_404(self, client):
        assert client.get("/api/does-not-exist").status_code == 404

    def test_openapi_schema_is_served(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/situation" in schema["paths"]
        assert "/api/history" in schema["paths"]


class TestFrontendResolution:
    """The repo-relative default breaks once the package is installed rather
    than run in place, so the override has to work."""

    def test_explicit_setting_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANDON_FRONTEND_DIST", str(tmp_path))
        get_settings.cache_clear()

        from andon.main import resolve_frontend_dist

        assert resolve_frontend_dist() == tmp_path.resolve()
        get_settings.cache_clear()

    def test_defaults_to_the_repo_layout(self, monkeypatch):
        monkeypatch.delenv("ANDON_FRONTEND_DIST", raising=False)
        get_settings.cache_clear()

        from andon.main import DEFAULT_FRONTEND_DIST, resolve_frontend_dist

        assert resolve_frontend_dist() == DEFAULT_FRONTEND_DIST
        assert DEFAULT_FRONTEND_DIST.name == "dist"
        get_settings.cache_clear()

    def test_path_traversal_cannot_escape_the_bundle(self, monkeypatch, tmp_path):
        bundle = tmp_path / "dist"
        bundle.mkdir()
        (bundle / "index.html").write_text("<html>app</html>")
        (tmp_path / "secret.txt").write_text("do not serve me")

        monkeypatch.setenv("ANDON_FRONTEND_DIST", str(bundle))
        monkeypatch.setenv("ANDON_LLM_ENABLED", "false")
        monkeypatch.setenv("ANDON_WEATHER_PROVIDERS", "mock")
        monkeypatch.setenv("ANDON_TRAFFIC_PROVIDERS", "mock")
        monkeypatch.setenv("ANDON_INCIDENT_PROVIDERS", "mock")
        monkeypatch.setenv("ANDON_RADAR_PROVIDERS", "none")
        monkeypatch.setenv("ANDON_ACCESS_GRAPH_SOURCE", "mock")
        monkeypatch.setenv("ANDON_DATABASE_PATH", str(tmp_path / "db.sqlite3"))
        get_settings.cache_clear()

        from andon.main import create_app

        with TestClient(create_app()) as local:
            escaped = local.get("/../secret.txt")
            assert "do not serve me" not in escaped.text
            assert local.get("/").text == "<html>app</html>"

        get_settings.cache_clear()


BODY = {"restaurant_name": "HSR Kitchen", "latitude": 12.912233, "longitude": 77.651282,
        "refresh_interval_seconds": 300}


class TestRestaurantEvidenceApi:
    def test_registration_is_idempotent_per_location(self, client):
        first = client.post("/api/restaurants", json=BODY)
        second = client.post("/api/restaurants", json={**BODY, "restaurant_name": "Renamed"})

        assert first.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert second.json()["name"] == "Renamed"

    def test_refresh_returns_a_provenance_rich_bundle(self, client):
        rid = client.post("/api/restaurants", json=BODY).json()["id"]
        bundle = client.post(f"/api/restaurants/{rid}/refresh").json()

        assert bundle["restaurant"]["id"] == rid
        assert bundle["refresh"]["providers_called"] >= 1
        assert len(bundle["access_graph"]["approaches"]) >= 2
        for obs in bundle["observations"]:
            for field in ("source_id", "observed_at", "received_at", "freshness_seconds", "confidence", "kind"):
                assert obs[field] is not None, field
            assert obs["kind"] in ("observation", "forecast")
            assert "relevance" in obs["spatial"]
        assert {"weather", "traffic", "incidents"} <= set(bundle["coverage"])

    def test_evidence_read_does_not_call_providers(self, client):
        rid = client.post("/api/restaurants", json=BODY).json()["id"]
        client.post(f"/api/restaurants/{rid}/refresh")
        before = client.get(f"/api/restaurants/{rid}/sources").json()
        client.get(f"/api/restaurants/{rid}/evidence")
        after = client.get(f"/api/restaurants/{rid}/sources").json()
        assert [s["checked_at"] for s in before] == [s["checked_at"] for s in after]

    def test_sources_changes_history_incidents_graph(self, client):
        rid = client.post("/api/restaurants", json=BODY).json()["id"]
        client.post(f"/api/restaurants/{rid}/refresh")

        sources = client.get(f"/api/restaurants/{rid}/sources").json()
        assert all(s["status"] in {"healthy", "stale", "degraded", "unavailable",
                                   "misconfigured", "unauthorized", "disabled"} for s in sources)
        assert isinstance(client.get(f"/api/restaurants/{rid}/changes").json(), list)
        history = client.get(f"/api/restaurants/{rid}/history", params={"category": "traffic.flow"}).json()
        assert history["observations"] and all(o["category"] == "traffic.flow" for o in history["observations"])
        assert isinstance(client.get(f"/api/restaurants/{rid}/incidents").json(), list)
        graph = client.get(f"/api/restaurants/{rid}/access-graph").json()
        assert graph["source"] == "mock" and graph["derivation"]

    def test_unknown_restaurant_is_404(self, client):
        assert client.get("/api/restaurants/r_nope/evidence").status_code == 404

    def test_situation_response_carries_its_evidence(self, client):
        body = client.get("/api/situation", params=BENGALURU).json()
        assert body["restaurant_id"].startswith("r_")
        assert body["evidence"]["restaurant"]["id"] == body["restaurant_id"]


def test_evidence_survives_an_app_restart(monkeypatch, tmp_path):
    for key, value in {
        "ANDON_WEATHER_PROVIDERS": "mock", "ANDON_TRAFFIC_PROVIDERS": "mock",
        "ANDON_INCIDENT_PROVIDERS": "mock", "ANDON_RADAR_PROVIDERS": "none",
        "ANDON_ACCESS_GRAPH_SOURCE": "mock", "ANDON_LLM_ENABLED": "false",
        "ANDON_DATABASE_PATH": str(tmp_path / "persist.sqlite3"), "ANDON_LOG_JSON": "false",
    }.items():
        monkeypatch.setenv(key, value)
    from andon.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as first:
        rid = first.post("/api/restaurants", json=BODY).json()["id"]
        stored = len(first.post(f"/api/restaurants/{rid}/refresh").json()["observations"])

    get_settings.cache_clear()
    with TestClient(create_app()) as second:  # a new process over the same file
        bundle = second.get(f"/api/restaurants/{rid}/evidence").json()
        assert len(bundle["observations"]) == stored > 0
        assert [r["id"] for r in second.get("/api/restaurants").json()] == [rid]
    get_settings.cache_clear()


class TestSourceDetail:
    def test_one_source_in_full(self, client):
        rid = client.post("/api/restaurants", json=BODY).json()["id"]
        client.post(f"/api/restaurants/{rid}/refresh")
        detail = client.get(f"/api/restaurants/{rid}/sources/mock-traffic").json()

        assert detail["source"]["id"] == "mock-traffic"
        assert detail["health"]["status"] == "healthy"
        assert detail["observations"] and all(o["source_id"] == "mock-traffic" for o in detail["observations"])
        assert len(detail["history"]) >= len(detail["observations"])

    def test_unknown_source_is_404(self, client):
        rid = client.post("/api/restaurants", json=BODY).json()["id"]
        assert client.get(f"/api/restaurants/{rid}/sources/nope").status_code == 404
