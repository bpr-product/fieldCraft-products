from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from source.dal.QuickBooksRepository import QuickBooksRepository
from source.service.QuickBooksService import QuickBooksService
from source.quickbooks.QuickBooksAuthentication import (
    QuickBooksOAuthClient,
    QuickBooksReconnectRequiredError,
)
from source.Utils import constants as const


logger = logging.getLogger(__name__)


# Increment when the capability-detection rules change. Stored capability
# snapshots from an older version are refreshed automatically.
_CAPABILITY_DETECTION_VERSION = 3


# UX-facing messages — clear and actionable, no internal jargon.
_FEATURE_UNAVAILABLE_MESSAGES: Dict[str, str] = {
    const.CAPABILITY_PROJECTS: (
        "Projects are not available on your current QuickBooks plan. "
        "Please upgrade to QuickBooks Plus or higher to enable project sync."
    ),
    const.CAPABILITY_INVENTORY: (
        "Inventory tracking is not available on your current QuickBooks plan. "
        "Please upgrade to QuickBooks Plus or higher to enable inventory sync."
    ),
}


class QBOFeatureUnavailableError(Exception):
    """Raised when a sync operation requires a feature the tenant's QuickBooks plan does not include."""

    def __init__(self, feature: str, message: str):
        super().__init__(message)
        self.feature = feature


class QuickBooksCapabilityService:
    """Detects which QuickBooks Online features are available for a connected tenant.

    QuickBooks Online subscription tiers control feature availability:
      - Bills (AP):  Essentials, Plus, Advanced
      - Projects:    Plus, Advanced
      - Inventory:   Plus, Advanced
      - Time tracking: company preference flag used by QuickBooks time workflows

    Capabilities are fetched from the QBO Preferences API at connect time and stored
    in additional_info["capabilities"]. Call fetch_and_save() again after a tenant
    upgrades or downgrades their subscription to reflect the new plan immediately.
    """

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.oauth_client = QuickBooksOAuthClient(repository=self.qbo_repo)

    def fetch_and_save(self, tenant_id: str, service_id: str) -> Dict[str, Any]:
        """Fetch capabilities from QBO Preferences and persist them. Returns the capabilities dict."""
        logger.debug("[QBOCapability] fetch_and_save started | tenant_id=%s service_id=%s", tenant_id, service_id)
        try:
            capabilities = self._fetch_from_qbo(tenant_id, service_id)
            self.qbo_repo.saveCapabilities(tenant_id, service_id, capabilities)
            logger.info(
                "[QBOCapability] capabilities saved | tenant_id=%s bills=%s projects=%s inventory=%s time_tracking=%s fetched_at=%s",
                tenant_id,
                capabilities.get(const.CAPABILITY_BILLS),
                capabilities.get(const.CAPABILITY_PROJECTS),
                capabilities.get(const.CAPABILITY_INVENTORY),
                capabilities.get(const.CAPABILITY_TIME_TRACKING),
                capabilities.get("fetchedAt"),
            )
            return capabilities
        except QuickBooksReconnectRequiredError:
            logger.warning("[QBOCapability] reconnect required while saving capabilities | tenant_id=%s", tenant_id, exc_info=True)
            raise
        except Exception as exc:
            logger.exception("[QBOCapability] failed to fetch and save capabilities | tenant_id=%s error=%s", tenant_id, exc)
            raise

    def get_capabilities(self, tenant_id: str, service_id: str) -> Dict[str, Any]:
        """Return current capability data, refreshing stale detection snapshots."""
        logger.debug("[QBOCapability] get_capabilities | tenant_id=%s service_id=%s", tenant_id, service_id)
        try:
            caps = self.qbo_repo.getCapabilities(tenant_id, service_id)
            if caps and caps.get("detectionVersion") == _CAPABILITY_DETECTION_VERSION:
                logger.debug(
                    "[QBOCapability] capability cache hit | tenant_id=%s fetched_at=%s version=%s",
                    tenant_id,
                    caps.get("fetchedAt"),
                    caps.get("detectionVersion"),
                )
                return caps

            if caps:
                logger.info(
                    "[QBOCapability] refreshing stale capability cache | tenant_id=%s stored_version=%s current_version=%s",
                    tenant_id,
                    caps.get("detectionVersion"),
                    _CAPABILITY_DETECTION_VERSION,
                )
            else:
                logger.debug("[QBOCapability] capability cache miss | tenant_id=%s", tenant_id)

            return self.fetch_and_save(tenant_id, service_id)
        except QuickBooksReconnectRequiredError:
            logger.warning("[QBOCapability] reconnect required while reading capabilities | tenant_id=%s", tenant_id, exc_info=True)
            raise
        except Exception as exc:
            logger.exception("[QBOCapability] failed to get capabilities | tenant_id=%s error=%s", tenant_id, exc)
            raise

    def require(self, tenant_id: str, service_id: str, capability: str) -> None:
        """Raise QBOFeatureUnavailableError if the capability is not enabled for this tenant.

        Uses cached capabilities (falls back to live fetch on cache miss). Fails open
        on any capability check error so a transient QBO Preferences API or DB failure
        never blocks a sync the tenant is entitled to run.
        """
        logger.debug("[QBOCapability] require capability | tenant_id=%s capability=%s", tenant_id, capability)
        try:
            caps = self.get_capabilities(tenant_id, service_id)
            enabled = caps.get(capability, True)
            logger.debug("[QBOCapability] require result | capability=%s enabled=%s", capability, enabled)
            if not enabled:
                message = _FEATURE_UNAVAILABLE_MESSAGES.get(
                    capability,
                    "This feature is not available on your current QuickBooks plan.",
                )
                logger.info("[QBOCapability] capability blocked | capability=%s", capability)
                raise QBOFeatureUnavailableError(feature=capability, message=message)
        except QBOFeatureUnavailableError:
            raise
        except Exception as exc:
            # Fail open — a capability check failure must not block a sync the tenant
            # is entitled to run. The sync itself will surface real connectivity issues.
            logger.warning(
                "[QBOCapability] capability check failed; allowing sync | capability=%s error=%s",
                capability,
                exc,
                exc_info=True,
            )

    def projects_available(self, tenant_id: str, service_id: str, *, refresh: bool = False) -> bool:
        """Return whether real QuickBooks Projects are enabled for the company.

        When refresh=True, the live QBO Preferences API is the source of truth.
        The Project API may accept requests even when the QBO Projects UI setting
        is off in sandbox, so a sync run must not override an explicit
        ProjectsEnabled=false preference with the GraphQL fallback.
        """
        if refresh:
            try:
                caps = self.fetch_and_save(tenant_id, service_id)
                enabled = self._capability_enabled(caps, const.CAPABILITY_PROJECTS)
                logger.debug("[QBOCapability] projects_available live result | tenant_id=%s enabled=%s", tenant_id, enabled)
                return enabled
            except QuickBooksReconnectRequiredError:
                raise
            except Exception as exc:
                logger.warning(
                    "[QBOCapability] projects_available live check failed | tenant_id=%s error=%s",
                    tenant_id,
                    exc,
                    exc_info=True,
                )
                return False

        try:
            caps = self.get_capabilities(tenant_id, service_id)
            enabled = self._capability_enabled(caps, const.CAPABILITY_PROJECTS)
            logger.debug("[QBOCapability] projects_available cached result | tenant_id=%s enabled=%s", tenant_id, enabled)
            return enabled
        except QuickBooksReconnectRequiredError:
            raise
        except Exception as exc:
            logger.warning("[QBOCapability] projects_available cached check failed | tenant_id=%s error=%s", tenant_id, exc, exc_info=True)
            return False

    def _capability_enabled(self, caps: Optional[Dict[str, Any]], capability: str) -> bool:
        if not isinstance(caps, dict):
            return False
        return bool(caps.get(capability))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_from_qbo(self, tenant_id: str, service_id: str) -> Dict[str, Any]:
        logger.debug("[QBOCapability] fetch from QBO started | tenant_id=%s service_id=%s", tenant_id, service_id)
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            realm_id = connection.get(const.KEY_REALM_ID)
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            env = str(connection.get(const.KEY_ENV) or "sandbox").strip().lower()
            logger.info(
                "[QBOCapability] QBO connection loaded | env=%s realm_id_present=%s refresh_token_present=%s",
                env,
                bool(realm_id),
                bool(refresh_token),
            )

            if not realm_id or not refresh_token:
                raise RuntimeError("QuickBooks is not connected (missing realmId or refreshToken).")

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)

            if not client_id or not client_secret:
                raise RuntimeError("QuickBooks client credentials are missing (ClientId/ClientSecret).")

            logger.debug("[QBOCapability] credentials loaded; refreshing access token")
            from source.Utils import QBODiscoveryClient
            token_url = QBODiscoveryClient.get_token_endpoint(env)

            access_token = self.oauth_client.refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                token_url=token_url,
                current_connection=connection,
            )
            logger.debug("[QBOCapability] access token obtained")

            base_url = QBODiscoveryClient.get_qbo_api_base(env)
            url = f"{base_url}/v3/company/{realm_id}/preferences"
            logger.debug("[QBOCapability] calling Preferences API | endpoint=/v3/company/<realm>/preferences")

            response = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
                timeout=20,
            )
            logger.debug("[QBOCapability] Preferences API response received | status=%s", response.status_code)

            if response.status_code != 200:
                logger.warning(
                    "[QBOCapability] Preferences API returned error | status=%s",
                    response.status_code,
                )
                raise RuntimeError(
                    f"QuickBooks Preferences API failed (HTTP {response.status_code})."
                )

            prefs = (response.json() or {}).get("Preferences") or {}
            logger.debug("[QBOCapability] Preferences keys returned | keys=%s", list(prefs.keys()))
            return _parse_preferences(prefs)

        except QuickBooksReconnectRequiredError:
            logger.warning("[QBOCapability] reconnect required while fetching preferences | tenant_id=%s", tenant_id, exc_info=True)
            raise
        except requests.Timeout:
            logger.warning("[QBOCapability] Preferences API timed out | tenant_id=%s", tenant_id, exc_info=True)
            raise RuntimeError("QuickBooks Preferences API timed out. Please try again.")
        except requests.RequestException as exc:
            logger.warning("[QBOCapability] Preferences API network error | tenant_id=%s error=%s", tenant_id, exc, exc_info=True)
            raise RuntimeError(f"Network error while contacting QuickBooks: {exc}") from exc
        except RuntimeError:
            logger.exception("[QBOCapability] Preferences API runtime error")
            raise
        except Exception as exc:
            logger.exception("[QBOCapability] unexpected error while fetching preferences | tenant_id=%s error=%s", tenant_id, exc)
            raise


def _other_pref_enabled(other_prefs: Dict[str, Any], name: str) -> bool:
    entries = (other_prefs or {}).get("NameValue") or []
    if isinstance(entries, dict):
        entries = [entries]

    expected = str(name or "").strip().lower()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_name = str(entry.get("Name") or "").strip().lower()
        if entry_name != expected:
            continue
        return _truthy_pref_value(entry.get("Value"))
    return False


def _truthy_pref_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _parse_preferences(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Extract feature availability flags from a QBO Preferences API response.

    Uses feature-flag signals per Intuit compliance guidance — does not hard-code
    subscription tier names so mid-cycle plan changes are reflected correctly on
    the next capabilities refresh.

    Bills/AP:   Do not infer from ProductAndServicesPrefs.ForPurchase. That
                preference controls item-based purchase forms, not Bill API access.
                Bill operations are allowed to reach QBO, which remains the source
                of truth for any actual entitlement or validation error.
    Projects:   OtherPrefs.NameValue[ProjectsEnabled] (True on Plus/Advanced only)
    Inventory:  ProductAndServicesPrefs.QuantityOnHand (True on Plus/Advanced only)
    Time:       OtherPrefs.NameValue[TimeTrackingFeatureEnabled]
    """
    product_prefs = prefs.get("ProductAndServicesPrefs") or {}
    other_prefs = prefs.get("OtherPrefs") or {}

    # ProductAndServicesPrefs.ForPurchase is a company preference for using
    # products/services on purchase forms. It is not a subscription entitlement
    # for the Bill entity. Blocking Bill sync with this value produces false
    # negatives for entitled companies (including Advanced). Let the Bill API
    # validate the operation instead.
    bills_enabled = True
    logger.debug(
        "[QBOCapability] parsed bills capability | bills_enabled=True source=bill_api"
    )

    # Projects — Plus and Advanced only. Explicit flag in OtherPrefs.
    projects_enabled = _other_pref_enabled(other_prefs, "ProjectsEnabled")
    if projects_enabled:
        logger.debug("[QBOCapability] parsed projects capability | projects_enabled=True")
    else:
        logger.debug("[QBOCapability] parsed projects capability | projects_enabled=False")

    # Inventory tracking — Plus and Advanced only.
    quantity_on_hand = product_prefs.get("QuantityOnHand")
    inventory_enabled = bool(quantity_on_hand)
    logger.debug(
        "[QBOCapability] parsed inventory capability | quantity_on_hand_present=%s inventory_enabled=%s",
        quantity_on_hand is not None,
        inventory_enabled,
    )

    # Company-level time tracking prerequisite used by Intuit's time workflows.
    time_tracking_enabled = _other_pref_enabled(other_prefs, "TimeTrackingFeatureEnabled")
    logger.debug(
        "[QBOCapability] parsed time tracking capability | time_tracking_enabled=%s",
        time_tracking_enabled,
    )

    result = {
        const.CAPABILITY_BILLS: bills_enabled,
        const.CAPABILITY_PROJECTS: projects_enabled,
        const.CAPABILITY_INVENTORY: inventory_enabled,
        const.CAPABILITY_TIME_TRACKING: time_tracking_enabled,
        "detectionVersion": _CAPABILITY_DETECTION_VERSION,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }
    logger.debug(
        "[QBOCapability] parsed capability result | bills=%s projects=%s inventory=%s time_tracking=%s fetched_at=%s",
        result.get(const.CAPABILITY_BILLS),
        result.get(const.CAPABILITY_PROJECTS),
        result.get(const.CAPABILITY_INVENTORY),
        result.get(const.CAPABILITY_TIME_TRACKING),
        result.get("fetchedAt"),
    )
    return result
