from unittest.mock import MagicMock

import pytest

from source.Utils import constants as const
from source.quickbooks.QuickBooksPayrollTestService import QuickBooksPayrollTestService


def _service_without_init() -> QuickBooksPayrollTestService:
    return QuickBooksPayrollTestService.__new__(QuickBooksPayrollTestService)


def _response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = "payload"
    response.headers = {}
    return response


def _context():
    return {
        "tenantId": "tenant", "serviceId": "6", "realmId": "realm",
        "env": "production", "baseUrl": "https://qbo", "accessToken": "AT",
        "localProductionOverride": {"enabled": False},
    }


def test_context_requires_connection_and_credentials():
    service = _service_without_init()
    service.qbo_repo = MagicMock()
    service.qbo_service = MagicMock()
    service.oauth_client = MagicMock()
    service.qbo_repo.getAdditionalInfo.return_value = {}
    service.qbo_service.get_quickbooks_config.return_value = {}

    with pytest.raises(ValueError, match="realmId.*refreshToken.*ClientId.*ClientSecret"):
        service._context("tenant", "6")


def test_company_info_is_read_only_and_returns_company(monkeypatch):
    service = _service_without_init()
    monkeypatch.setattr(service, "_context", lambda *a: _context())
    response = _response({"CompanyInfo": {"CompanyName": "FIELDCRAFT"}})
    module = __import__(service.__class__.__module__, fromlist=["qbo_api"])
    monkeypatch.setattr(module.qbo_api, "qbo_get", lambda *a, **k: response)

    result = service.company_info("tenant", "6")

    assert result[const.KEY_OK] is True
    assert result["companyInfo"]["CompanyName"] == "FIELDCRAFT"


def test_employees_caps_query_page_size(monkeypatch):
    service = _service_without_init()
    monkeypatch.setattr(service, "_context", lambda *a: _context())
    captured = {}
    response = _response({"QueryResponse": {"Employee": [{"Id": "1"}]}})
    module = __import__(service.__class__.__module__, fromlist=["qbo_api"])

    def fake_get(*args, **kwargs):
        captured.update(kwargs.get("params") or {})
        return response

    monkeypatch.setattr(module.qbo_api, "qbo_get", fake_get)
    result = service.employees("tenant", "6", max_results=500)

    assert "maxresults 100" in captured["query"]
    assert result["count"] == 1


def test_employee_compensations_returns_graphql_errors(monkeypatch):
    service = _service_without_init()
    monkeypatch.setattr(service, "_context", lambda *a: _context())
    response = _response({"errors": [{"message": "not entitled"}]})
    module = __import__(service.__class__.__module__, fromlist=["qbo_api"])
    monkeypatch.setattr(module.qbo_api, "qbo_post", lambda *a, **k: response)

    result = service.employee_compensations("tenant", "6", "EMP-1")

    assert result[const.KEY_OK] is False
    assert result["errors"][0]["message"] == "not entitled"


def test_employee_compensations_extracts_nodes(monkeypatch):
    service = _service_without_init()
    monkeypatch.setattr(service, "_context", lambda *a: _context())
    response = _response({
        "data": {"payrollEmployeeCompensations": {"edges": [
            {"node": {"id": "COMP-1", "active": True}}
        ]}}
    })
    module = __import__(service.__class__.__module__, fromlist=["qbo_api"])
    monkeypatch.setattr(module.qbo_api, "qbo_post", lambda *a, **k: response)

    result = service.employee_compensations("tenant", "6", "EMP-1")

    assert result[const.KEY_OK] is True
    assert result["count"] == 1
    assert result["compensations"][0]["id"] == "COMP-1"
