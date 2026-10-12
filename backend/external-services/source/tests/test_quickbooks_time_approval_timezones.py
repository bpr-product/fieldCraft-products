from source.quickbooks.QuickBooksTimeApprovalService import (
    QuickBooksTimeApprovalService,
)


def test_approval_target_keeps_all_project_timezones_for_same_worker_date():
    summary = {}

    QuickBooksTimeApprovalService.register_success(
        summary,
        user_id=11773320,
        work_date="2026-07-31",
        map_ids=[10],
        fieldcraft_user_account_id=55,
        timezone_name="America/Los_Angeles",
    )
    QuickBooksTimeApprovalService.register_success(
        summary,
        user_id=11773320,
        work_date="2026-07-31",
        map_ids=[11],
        fieldcraft_user_account_id=55,
        timezone_name="America/New_York",
    )

    target = next(
        iter(summary[QuickBooksTimeApprovalService.TARGETS_KEY].values())
    )
    assert target["mapIds"] == [10, 11]
    assert target["timezoneNames"] == [
        "America/Los_Angeles",
        "America/New_York",
    ]
    assert target["timezoneName"] == "America/Los_Angeles"


def test_approval_readiness_checks_every_timezone_not_legacy_singular(monkeypatch):
    monkeypatch.setenv(
        QuickBooksTimeApprovalService.ENABLED_ENV,
        "true",
    )
    summary = {}
    QuickBooksTimeApprovalService.register_success(
        summary,
        user_id=11773320,
        work_date="2026-07-31",
        map_ids=[10],
        fieldcraft_user_account_id=55,
        timezone_name="America/Los_Angeles",
    )
    QuickBooksTimeApprovalService.register_success(
        summary,
        user_id=11773320,
        work_date="2026-07-31",
        map_ids=[11],
        fieldcraft_user_account_id=55,
        timezone_name="America/New_York",
    )

    class _Repo:
        def __init__(self):
            self.readiness_timezones = []
            self.metadata_updates = []

        def list_pending_quickbooks_time_approval_targets(self, **_kwargs):
            return []

        def list_unresolved_approved_timelogs_through_date(
            self,
            *,
            timezone_name,
            **_kwargs,
        ):
            self.readiness_timezones.append(timezone_name)
            return []

        def update_quickbooks_time_approval_metadata(self, **kwargs):
            self.metadata_updates.append(kwargs)

    class _TimeApi:
        @staticmethod
        def approve_user_through_date(*, approved_to, **_kwargs):
            return {"user": {"approved_to": approved_to.isoformat()}}

        @staticmethod
        def get_user(_user_id):
            raise AssertionError("approval response should already be verified")

    repo = _Repo()
    QuickBooksTimeApprovalService().finalize(
        tenant_id="tn24047750",
        service_id=6,
        time_api=_TimeApi(),
        timelog_repo=repo,
        summary=summary,
        dry_run=False,
    )

    assert repo.readiness_timezones == [
        "America/Los_Angeles",
        "America/New_York",
    ]
    assert summary["approvalSucceeded"] == 1
