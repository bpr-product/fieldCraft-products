import json
from datetime import date, datetime, timezone

import pytest

from source.Utils.ProjectTimezoneResolver import (
    ProjectTimezoneResolutionError,
    ProjectTimezoneResolver,
)


class _FakeTimezoneFinder:
    def __init__(self):
        self.calls = []

    def timezone_at(self, *, lng, lat):
        self.calls.append((lat, lng))
        if lng < -100:
            return "America/Los_Angeles"
        if lng < -80:
            return "America/Chicago"
        return "America/New_York"


def _project_candidate(**overrides):
    candidate = {
        "project_id": 9033,
        "project_jobsite_lat": 34.0522,
        "project_jobsite_lng": -118.2437,
        "project_geocode_status": "GEOCODED",
        "project_location": None,
        "project_customer_asset": None,
        "start_time": "2026-08-01T02:00:00+00:00",
        "end_time": "2026-08-01T03:00:00+00:00",
        "user_account_id": 55,
    }
    candidate.update(overrides)
    return candidate


def test_resolves_iana_timezone_from_existing_project_coordinates():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)

    result = resolver.resolve(_project_candidate())

    assert result.timezone_name == "America/Los_Angeles"
    assert result.source == "PROJECT_JOBSITE_COORDINATES"
    assert result.latitude == pytest.approx(34.0522)
    assert result.longitude == pytest.approx(-118.2437)
    assert finder.calls == [(34.0522, -118.2437)]


def test_stored_mapping_timezone_wins_when_project_location_changes():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=40.7128,
        project_jobsite_lng=-74.0060,
        reason=json.dumps(
            {
                "dailyEntry": True,
                "dailyTimezone": "America/Los_Angeles",
                "dailyTimezoneSource": "PROJECT_JOBSITE_COORDINATES",
                "dailyWorkDate": "2026-07-31",
            }
        ),
    )

    result = resolver.resolve(candidate)

    assert result.timezone_name == "America/Los_Angeles"
    assert result.source == "MAPPING_SNAPSHOT"
    assert finder.calls == []




def test_legacy_approval_timezone_does_not_override_project_location():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_location="43.0906422,-89.28181479999999",
        reason=json.dumps(
            {
                "dailyEntry": True,
                "approvalTimezone": "America/Los_Angeles",
                "approvalWorkDate": "2026-07-27",
            }
        ),
    )

    result = resolver.resolve(candidate)

    assert result.timezone_name == "America/Chicago"
    assert result.source == "PROJECT_LOCATION"


def test_untrusted_daily_timezone_without_location_source_is_recalculated():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_location="43.0906422,-89.28181479999999",
        reason=json.dumps(
            {
                "dailyEntry": True,
                "dailyTimezone": "America/Los_Angeles",
            }
        ),
    )

    result = resolver.resolve(candidate)

    assert result.timezone_name == "America/Chicago"
    assert result.source == "PROJECT_LOCATION"


def test_project_location_coordinates_are_used_when_jobsite_columns_are_null():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_geocode_status="PENDING",
        project_location="43.0906422,-89.28181479999999",
    )

    result = resolver.resolve(candidate)

    assert result.timezone_name == "America/Chicago"
    assert result.source == "PROJECT_LOCATION"
    assert result.latitude == pytest.approx(43.0906422)
    assert result.longitude == pytest.approx(-89.2818148)
    assert finder.calls[0][0] == pytest.approx(43.0906422)
    assert finder.calls[0][1] == pytest.approx(-89.2818148)


def test_customer_asset_location_is_used_when_project_location_is_missing():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_geocode_status="PENDING",
        project_location=None,
        project_customer_asset={
            "address": "61 1st Cross Street",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "country": "India",
            "zipCode": "600100",
            "location": "12.942142174480326,80.20746702726073",
        },
    )

    result = resolver.resolve(candidate)

    assert result.source == "PROJECT_CUSTOMER_ASSET_LOCATION"
    assert result.latitude == pytest.approx(12.942142174480326)
    assert result.longitude == pytest.approx(80.20746702726073)
    assert finder.calls == [(12.942142174480326, 80.20746702726073)]


def test_customer_asset_json_string_coordinate_object_is_supported():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_location=None,
        project_customer_asset=json.dumps(
            {
                "address": "557 Jupiter Drive",
                "location": {"latitude": 43.0906422, "longitude": -89.2818148},
            }
        ),
    )

    result = resolver.resolve(candidate)

    assert result.source == "PROJECT_CUSTOMER_ASSET_LOCATION"
    assert result.timezone_name == "America/Chicago"


def test_wkt_project_location_is_supported():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_location="POINT(-118.2437 34.0522)",
    )

    result = resolver.resolve(candidate)

    assert result.source == "PROJECT_LOCATION"
    assert result.timezone_name == "America/Los_Angeles"


def test_missing_all_project_location_sources_is_not_replaced_with_utc():
    resolver = ProjectTimezoneResolver(finder=_FakeTimezoneFinder())
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_geocode_status="PENDING",
        project_location=None,
        project_customer_asset={
            "address": "Unknown address",
            "city": "",
            "state": "",
        },
    )

    with pytest.raises(ProjectTimezoneResolutionError) as exc_info:
        resolver.resolve(candidate)

    assert exc_info.value.code == "PROJECT_TIMEZONE_UNRESOLVED"
    assert "project 9033" in str(exc_info.value)


def test_zero_coordinate_values_are_valid_input_not_treated_as_missing():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)

    result = resolver.resolve(
        _project_candidate(
            project_jobsite_lat=0,
            project_jobsite_lng=0,
        )
    )

    assert result.timezone_name == "America/New_York"
    assert finder.calls == [(0.0, 0.0)]


def test_daily_work_date_uses_project_timezone_and_not_process_timezone(monkeypatch):
    # Import locally because the production module has unrelated service-level
    # dependencies that are supplied by the deployment image.
    from source.quickbooks.QuickBooksDailyTimelogExportService import (
        QuickBooksDailyTimelogExportService,
    )

    service = QuickBooksDailyTimelogExportService.__new__(
        QuickBooksDailyTimelogExportService
    )
    service.project_timezone_resolver = ProjectTimezoneResolver(
        finder=_FakeTimezoneFinder()
    )
    candidate = _project_candidate()

    monkeypatch.setenv("TZ", "Asia/Kolkata")
    first = service._work_date(candidate)
    monkeypatch.setenv("TZ", "UTC")
    second = service._work_date(candidate)

    assert first == date(2026, 7, 31)
    assert second == date(2026, 7, 31)
    assert not hasattr(service, "TIMEZONE_ENV")


def test_project_local_day_query_uses_dst_aware_utc_boundaries():
    from source.quickbooks.QuickBooksDailyTimelogExportService import (
        QuickBooksDailyTimelogExportService,
    )

    captured = {}

    class _Repo:
        def get_daily_worker_day_rows(self, **kwargs):
            captured.update(kwargs)
            return []

    service = QuickBooksDailyTimelogExportService.__new__(
        QuickBooksDailyTimelogExportService
    )
    service.timelog_repo = _Repo()

    service._load_rows_for_worker_day(
        tenant_id="tn24047750",
        service_id="6",
        user_account_id=55,
        work_date=date(2026, 11, 1),
        project_id=9033,
        realm_id="realm-1",
        timezone_name="America/Los_Angeles",
    )

    # DST ends on this local day, so its UTC window is 25 hours.
    assert captured["start_at"] == datetime(
        2026, 11, 1, 7, 0, tzinfo=timezone.utc
    )
    assert captured["end_at"] == datetime(
        2026, 11, 2, 8, 0, tzinfo=timezone.utc
    )


def test_cross_midnight_timelog_is_not_exported_as_one_payroll_day():
    from source.quickbooks.QuickBooksDailyTimelogExportService import (
        DailyTimelogValidationError,
        QuickBooksDailyTimelogExportService,
    )

    service = QuickBooksDailyTimelogExportService.__new__(
        QuickBooksDailyTimelogExportService
    )
    service.project_timezone_resolver = ProjectTimezoneResolver(
        finder=_FakeTimezoneFinder()
    )
    row = _project_candidate(
        timelog_id=12000,
        start_time="2026-08-01T06:30:00+00:00",
        end_time="2026-08-01T08:30:00+00:00",
        employee_sync_status="SYNCED",
        project_sync_status="SYNCED",
        role_name="Installer",
        service_sync_status="SYNCED",
    )

    with pytest.raises(DailyTimelogValidationError) as exc_info:
        service._validate_eligible_row(
            row,
            timezone_name="America/Los_Angeles",
        )

    assert exc_info.value.code == "TIMELOG_CROSSES_PROJECT_MIDNIGHT"


def test_daily_reason_snapshot_is_valid_json_and_reusable():
    from source.quickbooks.QuickBooksDailyTimelogExportService import (
        QuickBooksDailyTimelogExportService,
    )

    reason = QuickBooksDailyTimelogExportService._daily_reason(
        "QuickBooks daily TimeActivity created.",
        dailyUserAccountId=55,
        dailyWorkDate="2026-07-31",
        dailyProjectId=9033,
        dailyTimezone="America/Los_Angeles",
        dailyTimezoneSource="PROJECT_JOBSITE_COORDINATES",
    )
    payload = json.loads(reason)

    assert payload["dailyTimezone"] == "America/Los_Angeles"
    assert payload["dailyTimezoneSource"] == "PROJECT_JOBSITE_COORDINATES"
    assert (
        ProjectTimezoneResolver(finder=_FakeTimezoneFinder())
        .resolve({"reason": reason})
        .source
        == "MAPPING_SNAPSHOT"
    )


def test_invalid_untrusted_stored_timezone_is_recalculated_from_project_location():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        reason=json.dumps(
            {
                "dailyEntry": True,
                "dailyTimezone": "Pacific Standard Time",
                "dailyTimezoneSource": "LEGACY_ENVIRONMENT",
            }
        )
    )

    result = resolver.resolve(candidate)

    assert result.timezone_name == "America/Los_Angeles"
    assert result.source == "PROJECT_JOBSITE_COORDINATES"
    assert finder.calls == [(34.0522, -118.2437)]


def test_invalid_trusted_stored_timezone_remains_a_hard_failure():
    resolver = ProjectTimezoneResolver(finder=_FakeTimezoneFinder())
    candidate = _project_candidate(
        reason=json.dumps(
            {
                "dailyEntry": True,
                "dailyTimezone": "Pacific Standard Time",
                "dailyTimezoneSource": "PROJECT_JOBSITE_COORDINATES",
            }
        )
    )

    with pytest.raises(ProjectTimezoneResolutionError) as exc_info:
        resolver.resolve(candidate)

    assert exc_info.value.code == "PROJECT_TIMEZONE_SNAPSHOT_INVALID"


def test_rows_for_group_excludes_rows_from_another_project_timezone():
    from source.quickbooks.QuickBooksDailyTimelogExportService import (
        QuickBooksDailyTimelogExportService,
    )

    service = QuickBooksDailyTimelogExportService.__new__(
        QuickBooksDailyTimelogExportService
    )
    service.project_timezone_resolver = ProjectTimezoneResolver(
        finder=_FakeTimezoneFinder()
    )
    la_row = _project_candidate(timelog_id=1)
    ny_row = _project_candidate(
        timelog_id=2,
        project_jobsite_lat=40.7128,
        project_jobsite_lng=-74.0060,
    )

    rows = service._rows_for_group(
        [la_row, ny_row],
        group_key=(55, date(2026, 7, 31), 9033),
        timezone_name="America/Los_Angeles",
    )

    assert [row["timelog_id"] for row in rows] == [1]


def test_nested_customer_asset_address_location_is_supported():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_location=None,
        project_customer_asset={
            "assetAddress": {
                "address": "557 Jupiter Drive",
                "city": "Madison",
                "location": "43.0906422,-89.2818148",
            }
        },
    )

    result = resolver.resolve(candidate)

    assert result.source == "PROJECT_CUSTOMER_ASSET_LOCATION"
    assert result.timezone_name == "America/Chicago"


def test_geojson_point_uses_longitude_latitude_order():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_location=None,
        project_customer_asset={
            "location": {
                "type": "Point",
                "coordinates": [-118.2437, 34.0522],
            }
        },
    )

    result = resolver.resolve(candidate)

    assert result.source == "PROJECT_CUSTOMER_ASSET_LOCATION"
    assert result.latitude == pytest.approx(34.0522)
    assert result.longitude == pytest.approx(-118.2437)
    assert result.timezone_name == "America/Los_Angeles"


def test_flattened_asset_columns_resolve_as_customer_asset_source():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_location=None,
        project_customer_asset=None,
        asset_latitude=40.7128,
        asset_longitude=-74.0060,
    )

    result = resolver.resolve(candidate)

    assert result.source == "PROJECT_CUSTOMER_ASSET_LOCATION"
    assert result.latitude == pytest.approx(40.7128)
    assert result.longitude == pytest.approx(-74.0060)
    assert result.timezone_name == "America/New_York"


def test_geojson_feature_geometry_coordinates_uses_lng_lat_order():
    finder = _FakeTimezoneFinder()
    resolver = ProjectTimezoneResolver(finder=finder)
    candidate = _project_candidate(
        project_jobsite_lat=None,
        project_jobsite_lng=None,
        project_location=None,
        project_customer_asset=None,
        location={
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [-74.0060, 40.7128],
            },
        },
    )

    result = resolver.resolve(candidate)

    assert result.source == "PROJECT_LOCATION"
    assert result.latitude == pytest.approx(40.7128)
    assert result.longitude == pytest.approx(-74.0060)
    assert result.timezone_name == "America/New_York"


def test_timezone_lookup_cache_does_not_keep_resolver_instances_alive():
    import gc
    import weakref

    resolver = ProjectTimezoneResolver(finder=_FakeTimezoneFinder())
    assert resolver._timezone_at(34.0522, -118.2437) == "America/Los_Angeles"

    resolver_ref = weakref.ref(resolver)
    del resolver
    gc.collect()

    assert resolver_ref() is None


def _daily_service_for_flow_test(candidate, events):
    from contextlib import nullcontext

    from source.Utils import constants as const
    from source.dal.TimelogRepository import TimelogRepository
    from source.quickbooks.QuickBooksDailyTimelogExportService import (
        QuickBooksDailyTimelogExportService,
    )

    service = QuickBooksDailyTimelogExportService.__new__(
        QuickBooksDailyTimelogExportService
    )
    service.project_timezone_resolver = ProjectTimezoneResolver(
        finder=_FakeTimezoneFinder()
    )
    service.timelog_repo = TimelogRepository()
    service.project_repo = type(
        "ProjectRepo",
        (),
        {"get_mapping_sync_target": staticmethod(lambda row: row[const.KEY_PROJECT_SYNC_TARGET])},
    )()
    service.capability_service = type(
        "Capabilities",
        (),
        {"projects_available": staticmethod(lambda *_args, **_kwargs: True)},
    )()
    service._employee_sync_config = {}
    service._daily_group_lock = lambda **_kwargs: nullcontext()
    service._load_daily_group_rows = lambda **_kwargs: [candidate]
    service._invalid_worker_days = lambda **_kwargs: {}

    qbo_rows = {}
    next_id = [9000]

    def create_time_activity(**kwargs):
        events.append("create")
        next_id[0] += 1
        row = dict(kwargs["payload"])
        row["Id"] = str(next_id[0])
        row["SyncToken"] = "0"
        qbo_rows[row["Id"]] = row
        return dict(row)

    def update_time_activity(**kwargs):
        events.append("update")
        activity_id = str(kwargs["time_activity_id"])
        row = dict(kwargs["payload"])
        row["Id"] = activity_id
        row["SyncToken"] = str(int(kwargs["sync_token"]) + 1)
        qbo_rows[activity_id] = row
        return dict(row)

    def get_time_activity(**kwargs):
        row = qbo_rows.get(str(kwargs["time_activity_id"]))
        return dict(row) if row else None

    def delete_time_activity(**kwargs):
        events.append("delete")
        qbo_rows.pop(str(kwargs["time_activity_id"]), None)
        return True

    service.create_time_activity = create_time_activity
    service.update_time_activity = update_time_activity
    service.get_time_activity = get_time_activity
    service.delete_time_activity = delete_time_activity
    service._flow_qbo_rows = qbo_rows

    def save_pending_snapshot(*, eligible_rows, reason, **_kwargs):
        events.append("snapshot")
        for row in eligible_rows:
            if int(row.get("map_id") or 0) == int(candidate["map_id"]):
                candidate["sync_status"] = TimelogRepository.STATUS_PENDING_PUSH
                candidate["reason"] = reason

    def save_group_success(
        *,
        eligible_rows,
        clear_map_ids,
        time_activity_id,
        realm_id,
        sync_token,
        reason,
        clear_reason,
        **_kwargs,
    ):
        events.append("success")
        eligible_ids = {int(row.get("map_id") or 0) for row in eligible_rows}
        clear_ids = {int(value) for value in clear_map_ids}
        map_id = int(candidate["map_id"])
        if map_id in eligible_ids:
            candidate["external_time_activity_id"] = str(time_activity_id)
            candidate["external_time_timesheet_id"] = None
            candidate["mapping_external_entity_type"] = "TIME_ACTIVITY"
            candidate["external_account_id"] = realm_id
            candidate["sync_token"] = sync_token
            candidate["sync_status"] = TimelogRepository.STATUS_SYNCED
            candidate["reason"] = reason
        elif map_id in clear_ids:
            candidate["external_time_activity_id"] = None
            candidate["external_time_timesheet_id"] = None
            candidate["mapping_external_entity_type"] = "TIME_ACTIVITY"
            candidate["external_account_id"] = None
            candidate["sync_token"] = None
            candidate["sync_status"] = TimelogRepository.STATUS_PENDING_PUSH
            candidate["reason"] = clear_reason

    service._save_pending_snapshot = save_pending_snapshot
    service._save_group_success = save_group_success
    return service, const

def _daily_flow_candidate(**overrides):
    candidate = _project_candidate(
        map_id=501,
        timelog_id=12001,
        start_time=datetime(2026, 8, 1, 5, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 1, 5, 30, tzinfo=timezone.utc),
        approval_status="APPROVED",
        timelog_status="completed",
        timelog_invalid=False,
        entry_type="WORK",
        employee_sync_status="SYNCED",
        project_sync_status="SYNCED",
        service_sync_status="SYNCED",
        qbo_employee_id="83",
        qbo_project_id="809093613",
        qbo_parent_customer_id="71",
        qbo_service_id="6",
        project_sync_target="QBO_PROJECT",
        resource_type="W2",
        external_entity_type="EMPLOYEE",
        role_name="Installer",
        task_name="Roof installation",
        project_name="Project 9033",
        firstname="Wade",
        lastname="FIELDCRAFT",
        sync_status="PENDING_PUSH",
        external_time_activity_id=None,
        external_time_timesheet_id=None,
        mapping_external_entity_type=None,
    )
    candidate.update(overrides)
    return candidate


def _daily_flow_summary(const):
    from collections import defaultdict

    summary = defaultdict(int)
    summary[const.KEY_RESULTS] = []
    summary[const.KEY_FAILURES] = []
    return summary


def test_new_daily_time_activity_persists_timezone_snapshot_before_qbo_create():
    candidate = _daily_flow_candidate()
    events = []
    service, const = _daily_service_for_flow_test(candidate, events)
    summary = _daily_flow_summary(const)
    group_key = service._group_key(candidate)

    assert group_key == (55, date(2026, 7, 31), 9033)
    assert service._process_daily_group(
        group_key=group_key,
        triggers=[candidate],
        is_previous_group=False,
        tenant_id="tn24047750",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-qbo",
        access_token="token",
        dry_run=False,
        summary=summary,
    ) is True

    assert events[:3] == ["snapshot", "create", "success"]
    payload = json.loads(candidate["reason"])
    assert payload["dailyTimezone"] == "America/Los_Angeles"
    assert payload["dailyWorkDate"] == "2026-07-31"
    assert candidate["external_time_activity_id"] == "9001"
    assert candidate["mapping_external_entity_type"] == "TIME_ACTIVITY"


def test_service_reconciles_timezone_drift_without_duplicate_time_activity():
    candidate = _daily_flow_candidate()
    events = []
    service, const = _daily_service_for_flow_test(candidate, events)

    first_summary = _daily_flow_summary(const)
    service._process_candidates_as_daily_groups(
        candidates=[candidate],
        tenant_id="tn24047750",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-qbo",
        access_token="token",
        dry_run=False,
        summary=first_summary,
    )

    first_id = candidate["external_time_activity_id"]
    assert list(service._flow_qbo_rows) == [first_id]
    assert service._flow_qbo_rows[first_id]["TxnDate"] == "2026-07-31"

    # Simulate a pre-project-timezone mapping whose stored date is valid history
    # but whose timezone source is not trusted by the new resolver.
    legacy_reason = json.loads(candidate["reason"])
    legacy_reason["dailyTimezoneSource"] = "LEGACY_ENVIRONMENT"
    candidate["reason"] = json.dumps(legacy_reason, separators=(",", ":"))
    candidate["project_jobsite_lat"] = 40.7128
    candidate["project_jobsite_lng"] = -74.0060
    candidate["sync_status"] = "PENDING_PUSH"

    units, invalid = service._work_units([candidate])
    assert invalid == []
    assert [(key[1].isoformat(), previous) for key, _rows, previous in units] == [
        ("2026-07-31", True),
        ("2026-08-01", False),
    ]

    second_summary = _daily_flow_summary(const)
    service._process_candidates_as_daily_groups(
        candidates=[candidate],
        tenant_id="tn24047750",
        service_id="6",
        realm_id="realm-1",
        base_url="https://sandbox-qbo",
        access_token="token",
        dry_run=False,
        summary=second_summary,
    )

    assert len(service._flow_qbo_rows) == 1
    final_row = next(iter(service._flow_qbo_rows.values()))
    assert final_row["TxnDate"] == "2026-08-01"
    assert first_id not in service._flow_qbo_rows
    assert events.count("create") == 2
    assert events.count("delete") == 1
    assert second_summary[const.KEY_FAILURES] == []
