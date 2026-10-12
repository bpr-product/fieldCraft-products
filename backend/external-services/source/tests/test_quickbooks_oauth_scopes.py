from source.quickbooks.QuickBooksOAuthScopes import (
    QBO_ACCOUNTING_SCOPE,
    QBO_PAYROLL_COMPENSATION_SCOPE,
    QBO_PROJECT_SCOPE,
    resolve_qbo_scope_text,
    resolve_qbo_scopes,
)


def test_required_quickbooks_scopes_are_always_present_in_stable_order():
    assert resolve_qbo_scopes("") == [
        QBO_ACCOUNTING_SCOPE,
        QBO_PROJECT_SCOPE,
        QBO_PAYROLL_COMPENSATION_SCOPE,
    ]


def test_configured_scopes_support_commas_spaces_and_deduplication():
    assert resolve_qbo_scopes(
        f"openid, profile {QBO_ACCOUNTING_SCOPE} openid"
    ) == [
        QBO_ACCOUNTING_SCOPE,
        QBO_PROJECT_SCOPE,
        QBO_PAYROLL_COMPENSATION_SCOPE,
        "openid",
        "profile",
    ]


def test_scope_text_is_space_delimited():
    text = resolve_qbo_scope_text("openid,email")

    assert text.split() == [
        QBO_ACCOUNTING_SCOPE,
        QBO_PROJECT_SCOPE,
        QBO_PAYROLL_COMPENSATION_SCOPE,
        "openid",
        "email",
    ]
