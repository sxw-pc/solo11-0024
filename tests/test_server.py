from __future__ import annotations

VALID_RUN = {
    "profiles": ["profile_sales_a", "profile_sales_b"],
    "tolerance_abs": 0,
    "tolerance_rel": 0.1,
    "write_reports": False,
}


def test_index_is_local_and_has_no_external_assets(api_client) -> None:
    response = api_client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "口径校准台" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()


def test_tasks_starts_empty(api_client) -> None:
    response = api_client.get("/tasks")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {"count": 0, "tasks": []}


def test_run_creates_task_and_detail(api_client) -> None:
    response = api_client.post("/run", json=VALID_RUN)
    assert response.status_code == 200
    payload = response.get_json()
    task_id = payload["task_id"]
    assert payload["task"]["summary"]["over_tolerance_groups"] == 2
    assert payload["reports"] == []

    detail = api_client.get(f"/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.get_json()["task_id"] == task_id

    listing = api_client.get("/tasks").get_json()
    assert listing["count"] == 1
    assert listing["tasks"][0]["task_id"] == task_id


def test_run_writes_reports_when_enabled(api_client) -> None:
    body = dict(VALID_RUN, write_reports=True)
    response = api_client.post("/run", json=body)
    assert response.status_code == 200
    reports = response.get_json()["reports"]
    assert len(reports) == 2
    assert reports[0].endswith(".json")
    assert reports[1].endswith(".md")


def test_invalid_json_body(api_client) -> None:
    response = api_client.post("/run", data="{", content_type="application/json")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_json"


def test_missing_or_too_few_profiles(api_client) -> None:
    missing = api_client.post("/run", json={"write_reports": False})
    assert missing.status_code == 400
    assert missing.get_json()["error"]["code"] == "invalid_profiles"

    too_few = api_client.post(
        "/run", json={"profiles": ["profile_sales_a"], "write_reports": False}
    )
    assert too_few.status_code == 400
    assert too_few.get_json()["error"]["code"] == "too_few_profiles"


def test_unknown_or_path_profile_rejected(api_client) -> None:
    unknown = api_client.post(
        "/run", json={"profiles": ["profile_sales_a", "profile_nope"], "write_reports": False}
    )
    assert unknown.status_code == 400
    assert unknown.get_json()["error"]["code"] == "profile_not_found"

    traversal = api_client.post(
        "/run", json={"profiles": ["profile_sales_a", "../sales"], "write_reports": False}
    )
    assert traversal.status_code == 400


def test_invalid_tolerance_and_unknown_field(api_client) -> None:
    bad_tolerance = api_client.post(
        "/run",
        json={"profiles": ["profile_sales_a", "profile_sales_b"], "tolerance_abs": True},
    )
    assert bad_tolerance.status_code == 400
    assert bad_tolerance.get_json()["error"]["code"] == "invalid_tolerance"

    extra = api_client.post(
        "/run",
        json={"profiles": ["profile_sales_a", "profile_sales_b"], "dataset_dir": "/tmp"},
    )
    assert extra.status_code == 400
    assert extra.get_json()["error"]["code"] == "unknown_field"


def test_missing_task_and_bad_limit(api_client) -> None:
    assert api_client.get("/tasks/kj-missing").status_code == 404
    bad_limit = api_client.get("/tasks?limit=0")
    assert bad_limit.status_code == 400
    non_integer = api_client.get("/tasks?limit=abc")
    assert non_integer.status_code == 400
