from unittest.mock import MagicMock

import pytest

from source.quickbooks.QuickBooksPayrollCompensationService import (
    QuickBooksPayrollCompensationService,
)


def _response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.reason = "OK" if status_code == 200 else "Error"
    response.url = "https://qb.api.intuit.com/graphql"
    response.headers = {"Content-Type": "application/json"}
    response.text = "payload"
    response.json.return_value = payload
    return response


def test_resolve_regular_pay_uses_employee_compensation_id(monkeypatch):
    service = QuickBooksPayrollCompensationService()
    response = _response(
        {
            "data": {
                "payrollEmployeeCompensations": {
                    "edges": [
                        {
                            "node": {
                                "id": "626270109",
                                "active": True,
                                "employerCompensation": {
                                    "id": "employer-pay-1",
                                    "name": "Regular Pay",
                                    "type": {
                                        "key": "REGULAR_PAY",
                                        "description": "Regular Pay",
                                        "value": "Regular Pay",
                                    },
                                },
                            }
                        }
                    ]
                }
            }
        }
    )
    module = __import__(service.__class__.__module__, fromlist=["qbo_api"])
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return response

    monkeypatch.setattr(module.qbo_api, "qbo_post", fake_post)

    result = service.resolve_regular_pay(
        access_token="access-token",
        qbo_employee_id="83",
    )

    assert result == {"id": "626270109", "name": "Regular Pay"}
    assert result["id"] != "employer-pay-1"
    assert captured["url"] == "https://qb.api.intuit.com/graphql"
    assert captured["json"]["variables"]["filter"] == {
        "employeeId": "83",
        "active": True,
    }


def test_resolve_regular_pay_rejects_missing_regular_pay(monkeypatch):
    service = QuickBooksPayrollCompensationService()
    response = _response(
        {
            "data": {
                "payrollEmployeeCompensations": {
                    "edges": [
                        {
                            "node": {
                                "id": "ot-1",
                                "active": True,
                                "employerCompensation": {
                                    "name": "Overtime Pay",
                                    "type": {"value": "Overtime Pay"},
                                },
                            }
                        }
                    ]
                }
            }
        }
    )
    module = __import__(service.__class__.__module__, fromlist=["qbo_api"])
    monkeypatch.setattr(module.qbo_api, "qbo_post", lambda *args, **kwargs: response)

    with pytest.raises(ValueError, match="does not have an active 'Regular Pay'"):
        service.resolve_regular_pay(
            access_token="access-token",
            qbo_employee_id="83",
        )


def test_resolve_regular_pay_rejects_multiple_active_regular_pay(monkeypatch):
    service = QuickBooksPayrollCompensationService()
    response = _response(
        {
            "data": {
                "payrollEmployeeCompensations": {
                    "edges": [
                        {
                            "node": {
                                "id": "regular-1",
                                "active": True,
                                "employerCompensation": {
                                    "name": "Regular Pay",
                                    "type": {"value": "Regular Pay"},
                                },
                            }
                        },
                        {
                            "node": {
                                "id": "regular-2",
                                "active": True,
                                "employerCompensation": {
                                    "name": "Regular Pay",
                                    "type": {"value": "Regular Pay"},
                                },
                            }
                        },
                    ]
                }
            }
        }
    )
    module = __import__(service.__class__.__module__, fromlist=["qbo_api"])
    monkeypatch.setattr(module.qbo_api, "qbo_post", lambda *args, **kwargs: response)

    with pytest.raises(ValueError, match="multiple active 'Regular Pay'"):
        service.resolve_regular_pay(
            access_token="access-token",
            qbo_employee_id="83",
        )


def test_active_compensations_are_cached_per_access_token_and_employee(monkeypatch):
    service = QuickBooksPayrollCompensationService()
    response = _response(
        {
            "data": {
                "payrollEmployeeCompensations": {
                    "edges": [
                        {
                            "node": {
                                "id": "626270109",
                                "active": True,
                                "employerCompensation": {
                                    "name": "Regular Pay",
                                    "type": {"value": "Regular Pay"},
                                },
                            }
                        }
                    ]
                }
            }
        }
    )
    module = __import__(service.__class__.__module__, fromlist=["qbo_api"])
    post = MagicMock(return_value=response)
    monkeypatch.setattr(module.qbo_api, "qbo_post", post)

    first = service.resolve_regular_pay(
        access_token="access-token",
        qbo_employee_id="83",
    )
    second = service.resolve_regular_pay(
        access_token="access-token",
        qbo_employee_id="83",
    )

    assert first == second == {"id": "626270109", "name": "Regular Pay"}
    post.assert_called_once()
