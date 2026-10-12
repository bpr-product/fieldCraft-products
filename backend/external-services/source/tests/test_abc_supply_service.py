import pytest

from source.service.ABCSupplyService import ABCSupplyConfigError, ABCSupplyService


@pytest.fixture(autouse=True)
def _abc_runtime_env(monkeypatch):
    monkeypatch.setenv("ABC_SUPPLY_ENV", "sandbox")
    monkeypatch.setenv("ABC_SUPPLY_SCOPES", "product.read pricing.read account.read location.read")


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.content = b"{}"
        self.text = "{}"

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, data=None, json=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "data": data, "json": json})
        if url.endswith("/token"):
            return FakeResponse(payload={"access_token": "abc-token", "expires_in": 1800})
        return FakeResponse(payload={"ok": True, "echo": json})

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return FakeResponse(payload={"ok": True})


class RefreshTokenSession:
    def __init__(self, revoked_method="POST"):
        self.calls = []
        self.revoked_method = revoked_method
        self._token_count = 0
        self._api_count = 0

    def post(self, url, headers=None, data=None, json=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "data": data, "json": json})
        if url.endswith("/token"):
            self._token_count += 1
            return FakeResponse(payload={"access_token": f"abc-token-{self._token_count}", "expires_in": 1800})
        if self.revoked_method == "POST" and self._api_count == 0:
            self._api_count += 1
            return FakeResponse(status_code=401, payload={"error": "Token has been revoked."})
        self._api_count += 1
        return FakeResponse(payload={"ok": True, "echo": json})

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        if self.revoked_method == "GET" and self._api_count == 0:
            self._api_count += 1
            return FakeResponse(status_code=401, payload={"error": "Token has been revoked."})
        self._api_count += 1
        return FakeResponse(payload={"ok": True})


class FakeRepository:
    def __init__(self, info=None):
        self.info = info or {}
        self.saved = []
        self.disconnected = []

    def resolve_service_id(self, service_id=None):
        return str(service_id or "7")

    def get_additional_info(self, subscriber_id, service_id):
        return dict(self.info)

    def save_connection(self, subscriber_id, service_id, config, validated_at=None):
        self.saved.append((subscriber_id, service_id, dict(config), validated_at))
        return {
            "connected": True,
            "status": "connected",
            "branchNumber": config.get("branch_number"),
            "clientIdPresent": True,
            "clientSecretPresent": True,
            "validatedAt": validated_at,
        }

    def get_status(self, subscriber_id, service_id):
        return {
            "connected": self.info.get("status") == "connected",
            "status": self.info.get("status") or "disconnected",
            "clientIdPresent": bool(self.info.get("clientId")),
            "clientSecretPresent": bool(self.info.get("clientSecret")),
            "branchNumber": self.info.get("branchNumber"),
        }

    def disconnect(self, subscriber_id, service_id, clear_credentials=False):
        self.disconnected.append((subscriber_id, service_id, clear_credentials))
        return {"connected": False, "status": "disconnected"}


class _TestableABCSupplyService(ABCSupplyService):
    def get_abc_config(self, tenant_id, service_id):
        return self._normalize_config(
            {
                "ClientId": "client-id",
                "ClientSecret": "client-secret",
                "BranchNumber": "17",
                "ShipToNumber": "2154884-2",
                "BillToNumber": "2154884-2",
            }
        )


def test_build_search_items_payload_with_query_and_branch():
    service = ABCSupplyService()
    payload = service._build_search_items_payload(
        query="shingle",
        item_number=None,
        branch_number="17",
        items_per_page=20,
        page_number=1,
        embed_branches=True,
        raw_filters=None,
    )

    assert payload["pagination"] == {"itemsPerPage": 20, "pageNumber": 1}
    assert payload["embed"] == ["branches"]
    assert payload["filters"][0]["key"] == "itemDescription"
    assert payload["filters"][0]["condition"] == "contains"
    assert payload["filters"][0]["values"] == ["shingle"]
    assert payload["filters"][1]["key"] == "branchNumber"
    assert payload["filters"][1]["condition"] == "equals"
    assert payload["filters"][1]["values"] == ["17"]


def test_search_items_calls_token_then_product_search():
    fake_session = FakeSession()
    service = _TestableABCSupplyService(session=fake_session)

    response = service.search_items(
        tenant_id="tenant-1",
        service_id="svc-1",
        query="gaf",
        items_per_page=10,
        page_number=2,
        include_family_items=True,
    )

    assert response["ok"] is True
    assert fake_session.calls[0]["url"].endswith("/token")
    assert fake_session.calls[0]["data"]["grant_type"] == "client_credentials"
    assert fake_session.calls[1]["url"] == "https://partners-sb.abcsupply.com/api/product/v1/search/items?familyItems=true"
    assert fake_session.calls[1]["headers"]["Authorization"] == "Bearer abc-token"
    assert fake_session.calls[1]["json"]["pagination"] == {"itemsPerPage": 10, "pageNumber": 2}


def test_price_items_uses_configured_branch_and_ship_to():
    fake_session = FakeSession()
    service = _TestableABCSupplyService(session=fake_session)

    service.price_items(
        tenant_id="tenant-1",
        service_id="svc-1",
        request_id="test-1",
        lines=[{"id": "1", "itemNumber": "02GASTZ3WW", "quantity": 1, "uom": "BD"}],
    )

    call = fake_session.calls[1]
    assert call["url"] == "https://partners-sb.abcsupply.com/api/pricing/v2/prices"
    assert call["json"]["branchNumber"] == "17"
    assert call["json"]["shipToNumber"] == "2154884-2"
    assert call["json"]["purpose"] == "estimating"
    assert call["json"]["lines"][0]["itemNumber"] == "02GASTZ3WW"


def test_get_abc_config_reads_credentials_from_env_when_explicitly_allowed(monkeypatch):
    monkeypatch.setenv("ABC_SUPPLY_ALLOW_ENV_FALLBACK", "true")
    monkeypatch.setenv("ABC_SUPPLY_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("ABC_SUPPLY_CLIENT_SECRET", "env-client-secret")
    monkeypatch.setenv("ABC_SUPPLY_ENV", "sandbox")
    monkeypatch.setenv("ABC_SUPPLY_BRANCH_NUMBER", "17")
    monkeypatch.setenv("ABC_SUPPLY_SHIP_TO_NUMBER", "2154884-2")

    config = ABCSupplyService().get_abc_config()

    assert config["client_id"] == "env-client-id"
    assert config["client_secret"] == "env-client-secret"
    assert config["branch_number"] == "17"
    assert config["ship_to_number"] == "2154884-2"


def test_get_abc_config_requires_tenant_connection_when_env_fallback_disabled(monkeypatch):
    monkeypatch.delenv("ABC_SUPPLY_ALLOW_ENV_FALLBACK", raising=False)
    with pytest.raises(ABCSupplyConfigError):
        ABCSupplyService().get_abc_config()


def test_get_abc_config_reads_connected_service_subscriber():
    repo = FakeRepository({
        "status": "connected",
        "clientId": "tenant-client",
        "clientSecret": "tenant-secret",
        "branchNumber": "17",
        "shipToNumber": "2154884-2",
        "billToNumber": "2154884-2",
    })
    service = ABCSupplyService(repository=repo)

    config = service.get_abc_config("tn1", "7")

    assert config["client_id"] == "tenant-client"
    assert config["client_secret"] == "tenant-secret"
    assert config["branch_number"] == "17"


def test_connect_tenant_validates_token_and_saves_connection():
    fake_session = FakeSession()
    repo = FakeRepository()
    service = ABCSupplyService(session=fake_session, repository=repo)

    status = service.connect_tenant(
        tenant_id="tn1",
        client_id="abc-client",
        client_secret="abc-secret",
        branch_number="17",
        ship_to_number="2154884-2",
        bill_to_number="2154884-2",
    )

    assert status["connected"] is True
    assert fake_session.calls[0]["url"].endswith("/token")
    assert repo.saved[0][0] == "tn1"
    assert repo.saved[0][1] == "7"
    assert repo.saved[0][2]["client_id"] == "abc-client"
    assert repo.saved[0][2]["client_secret"] == "abc-secret"


def test_disconnect_tenant_updates_status():
    repo = FakeRepository({"status": "connected"})
    service = ABCSupplyService(repository=repo)

    status = service.disconnect_tenant("tn1", clear_credentials=True)

    assert status == {"connected": False, "status": "disconnected"}
    assert repo.disconnected == [("tn1", "7", True)]


def test_account_materials_recent_uses_bill_to_branch_and_pagination():
    fake_session = FakeSession()
    service = _TestableABCSupplyService(session=fake_session)

    service.get_account_items(
        tenant_id="tenant-1",
        service_id="svc-1",
        list_type="recent",
        items_per_page=100,
        page_number=3,
    )

    call = fake_session.calls[1]
    assert call["method"] == "GET"
    assert call["url"] == (
        "https://partners-sb.abcsupply.com/api/product/v1/items/2154884-2/recents"
        "?itemsPerPage=100&pageNumber=3&branchNumber=17"
    )


def test_account_materials_frequent_endpoint():
    fake_session = FakeSession()
    service = _TestableABCSupplyService(session=fake_session)

    service.get_account_items(
        tenant_id="tenant-1",
        service_id="svc-1",
        list_type="frequent",
        bill_to_number="2154884-2",
        branch_number="17",
    )

    assert fake_session.calls[1]["url"].startswith(
        "https://partners-sb.abcsupply.com/api/product/v1/items/2154884-2/frequents"
    )


def test_get_all_items_endpoint_with_embeds():
    fake_session = FakeSession()
    service = _TestableABCSupplyService(session=fake_session)

    service.get_all_items(
        tenant_id="tenant-1",
        service_id="svc-1",
        items_per_page=1000,
        page_number=1,
        embed_branches=True,
        embed_variations=True,
    )

    assert fake_session.calls[1]["url"] == (
        "https://partners-sb.abcsupply.com/api/product/v1/items"
        "?itemsPerPage=1000&pageNumber=1&embed=branches%2Cvariations"
    )


def test_search_accounts_ship_to_default():
    fake_session = FakeSession()
    service = _TestableABCSupplyService(session=fake_session)

    service.search_accounts(tenant_id="tenant-1", service_id="svc-1")

    call = fake_session.calls[1]
    assert call["url"] == "https://partners-sb.abcsupply.com/api/account/v1/search/accounts"
    assert call["json"]["filters"][0] == {
        "key": "accountType",
        "condition": "equals",
        "values": ["Ship-To"],
    }
    assert call["json"]["filters"][1]["key"] == "storefront"


def test_normalize_item_extracts_supplier_hierarchy_uom_and_branch_availability():
    service = ABCSupplyService()
    item = {
        "itemNumber": "01CT25CS",
        "supplierName": "CertainTeed",
        "itemDescription": "CertainTeed XT 25 Algae Resistant Shingles Colonial Slate 3 Bundle Per Square",
        "uoms": [
            {"name": "Square", "code": "SQ", "description": "costing"},
            {"name": "Bundle", "code": "BD", "description": "stocking"},
        ],
        "hierarchy": {
            "productGroup": {
                "category": {
                    "label": "Steep Slope Roofing (>2:12 Pitch)",
                    "productType": {"label": "Fiberglass Laminated Shingles"},
                }
            }
        },
        "branches": [{"number": "17"}],
    }

    normalized = service._normalize_item(item, branch_number="17")

    assert normalized["manufacturerName"] == "CertainTeed"
    assert normalized["categoryName"] == "Steep Slope Roofing (>2:12 Pitch)"
    assert normalized["productTypeName"] == "Fiberglass Laminated Shingles"
    assert normalized["uom"] == "BD"
    assert normalized["uomDisplay"] == "BD - Bundle"
    assert normalized["branchAvailability"] == "Available at Branch 17"


def test_normalize_price_response_uses_unit_price_and_currency_object():
    service = ABCSupplyService()
    response = {
        "lines": [
            {
                "id": "1",
                "itemNumber": "01CT25CS",
                "quantity": 1,
                "uom": "BD",
                "unitPrice": 42.25,
                "currency": {"code": "USD", "symbol": "$"},
                "status": {"code": "OK", "message": "Priced Successfully"},
            }
        ]
    }

    normalized = service._normalize_price_response(response)

    assert normalized["lines"][0]["netPrice"] == 42.25
    assert normalized["lines"][0]["currencyCode"] == "USD"
    assert normalized["lines"][0]["statusMessage"] == "Priced Successfully"


def test_build_search_items_payload_rejects_more_than_two_filters():
    service = ABCSupplyService()

    with pytest.raises(ValueError) as exc:
        service._build_search_items_payload(
            query="shingle",
            item_number="01CT25CS",
            branch_number="17",
            items_per_page=20,
            page_number=1,
            embed_branches=False,
            raw_filters=None,
        )

    assert "supports at most 2 filters" in str(exc.value)
    assert "received 3" in str(exc.value)


def test_post_refreshes_token_once_when_abc_revokes_cached_token():
    fake_session = RefreshTokenSession(revoked_method="POST")
    service = _TestableABCSupplyService(session=fake_session)

    response = service.search_items(
        tenant_id="tenant-1",
        service_id="svc-1",
        query="gaf",
        items_per_page=10,
        page_number=1,
    )

    assert response["ok"] is True
    token_calls = [call for call in fake_session.calls if call["url"].endswith("/token")]
    api_calls = [call for call in fake_session.calls if "/api/product/v1/search/items" in call["url"]]

    assert len(token_calls) == 2
    assert len(api_calls) == 2
    assert api_calls[0]["headers"]["Authorization"] == "Bearer abc-token-1"
    assert api_calls[1]["headers"]["Authorization"] == "Bearer abc-token-2"


def test_get_refreshes_token_once_when_abc_revokes_cached_token():
    fake_session = RefreshTokenSession(revoked_method="GET")
    service = _TestableABCSupplyService(session=fake_session)

    response = service.get_item_availability(
        tenant_id="tenant-1",
        service_id="svc-1",
        item_number="01CT25CS",
    )

    assert response["ok"] is True
    token_calls = [call for call in fake_session.calls if call["url"].endswith("/token")]
    api_calls = [call for call in fake_session.calls if "/api/product/v1/availability/items" in call["url"]]

    assert len(token_calls) == 2
    assert len(api_calls) == 2
    assert api_calls[0]["headers"]["Authorization"] == "Bearer abc-token-1"
    assert api_calls[1]["headers"]["Authorization"] == "Bearer abc-token-2"
