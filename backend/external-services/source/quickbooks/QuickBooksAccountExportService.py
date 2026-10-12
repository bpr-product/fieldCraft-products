import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.service.QuickBooksService import QuickBooksService
from source.Utils import constants as const
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.quickbooks.qbo_query_utils import escape_qbo_literal
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)


class QuickBooksAccountExportService:
    """Export FIELDCRAFT chart_of_account rows -> QuickBooks Online Accounts.

    Behavior:
      - invalid rows are skipped
      - mapped rows are updated in QBO using SyncToken when the local payload differs
      - unmapped rows are matched against QBO first, then created when missing
      - mapping remains stored directly on chart_of_account
    """

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.coa_repo = ChartOfAccountRepository()

    def export_accounts(self, *, tenant_id: str, service_id: str, limit: int = 500, offset: int = 0, object_id: Optional[str] = None, email: Optional[str] = None, user_role: Optional[str] = None) -> Dict[str, Any]:
        logger.info(
            "[QBO-ACCOUNT-EXPORT] START ts=%s tenant_id=%s service_id=%s limit=%s offset=%s",
            datetime.now(timezone.utc).isoformat(),
            tenant_id,
            service_id,
            limit,
            offset,
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="account_export_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_account",
        )
        # Track final outcome so the finally block logs it regardless of which exit path is taken.
        _audit_action = "account_export_failed"
        _audit_status = "failed"
        _audit_context: Optional[str] = None
        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()

            if not refresh_token or not realm_id:
                _audit_action, _audit_context = "account_export_aborted", "missing_realmId_or_refreshToken"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected for this tenant (missing refreshToken/realmId)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                _audit_action, _audit_context = "account_export_aborted", "missing_credentials"
                return {const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self.refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            base_url = self.get_qbo_api_base(env)
            accounts = self.coa_repo.get_accounts_for_tenant(tenant_id, limit=limit, offset=offset)
            summary: Dict[str, Any] = {
                const.KEY_OK: True,
                "tenantId": tenant_id,
                "serviceId": service_id,
                "realmIdPresent": bool(realm_id),
                "env": env,
                "requested": len(accounts),
                "skippedInvalid": 0,
                "skippedAlreadyCurrent": 0,
                "mappedFromQbo": 0,
                "createdInQbo": 0,
                "updatedInQbo": 0,
                "failed": 0,
                const.KEY_FAILURES: [],
                const.KEY_RESULTS: [],
            }

            for account in accounts:
                account_id = account.get("id")
                detail: Dict[str, Any] = {
                    const.KEY_FIELDCRAFT_ACCOUNT_ID: account_id,
                    "name": self._clean(account.get("name")),
                    "accountNo": self._clean(account.get("account_no")) or None,
                    "accountType": self._clean(account.get("account_type")) or None,
                    "detailType": self._clean(account.get("detail_type")) or None,
                    const.KEY_QBO_ACCOUNT_ID: self._clean(account.get("external_id")) or None,
                    const.KEY_SYNC_TOKEN: self._clean(account.get("sync_token")) or None,
                    const.KEY_STATUS: None,
                    const.KEY_ACTION: None,
                    const.KEY_REASON: None,
                    const.KEY_ERROR: None,
                }
                try:
                    qbo_api.clear_last_intuit_tid()
                    if bool(account.get("invalid")):
                        summary["skippedInvalid"] += 1
                        continue
                    if not self._clean(account.get("name")):
                        raise Exception("Missing account name")
                    account_type = self._clean(account.get("account_type"))
                    detail_type = self._clean(account.get("detail_type")) or None
                    if not account_type:
                        raise Exception("Missing account_type")

                    payload = self.build_qbo_account_payload(account)
                    existing_external_id = self._clean(account.get("external_id"))
                    existing_realm = self._clean(account.get("external_acc_id"))
                    existing_service_id = self._clean(account.get("service_id"))

                    if existing_external_id and existing_realm == str(realm_id) and existing_service_id == str(service_id):
                        current_qbo = None
                        try:
                            current_qbo = self.get_qbo_account_by_id(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                account_id=existing_external_id,
                            )
                        except Exception as exc:
                            if not self._is_qbo_stale_reference_error(str(exc)):
                                raise
                        if current_qbo and self._qbo_account_matches_payload(current_qbo, payload):
                            self.coa_repo.upsert_mapping(
                                tenant_id,
                                account_id=int(account_id),
                                external_id=existing_external_id,
                                sync_token=self._clean(current_qbo.get(const.QBO_KEY_SYNC_TOKEN)) or self._clean(account.get("sync_token")) or None,
                                service_id=str(service_id),
                                external_acc_id=str(realm_id),
                            )
                            summary["skippedAlreadyCurrent"] += 1
                            detail[const.KEY_STATUS] = "skipped"
                            detail[const.KEY_ACTION] = "alreadyCurrent"
                            detail[const.KEY_REASON] = "Mapped account already matches QuickBooks"
                            summary[const.KEY_RESULTS].append(detail)
                            continue
                        if current_qbo:
                            updated = self.update_account_in_qbo(
                                access_token=access_token,
                                base_url=base_url,
                                realm_id=str(realm_id),
                                account_id=existing_external_id,
                                payload=payload,
                                sync_token=self._clean(account.get("sync_token")) or None,
                            )
                            updated_id = self._clean(updated.get(const.QBO_KEY_ID)) or existing_external_id
                            updated_token = self._clean(updated.get(const.QBO_KEY_SYNC_TOKEN)) or None
                            self.coa_repo.upsert_mapping(
                                tenant_id,
                                account_id=int(account_id),
                                external_id=updated_id,
                                sync_token=updated_token,
                                service_id=str(service_id),
                                external_acc_id=str(realm_id),
                            )
                            summary["updatedInQbo"] += 1
                            detail[const.KEY_STATUS] = "success"
                            detail[const.KEY_ACTION] = "updatedInQbo"
                            detail[const.KEY_QBO_ACCOUNT_ID] = updated_id
                            detail[const.KEY_SYNC_TOKEN] = updated_token
                            detail[const.KEY_REASON] = "Updated mapped QuickBooks account"
                            summary[const.KEY_RESULTS].append(detail)
                            continue
                        # stale mapping: continue to remap/create

                    found = self.find_account_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        name=self._clean(account.get("name")),
                        acct_num=self._clean(account.get("account_no")),
                        account_type=account_type,
                        detail_type=detail_type,
                    )
                    if found and found.get(const.QBO_KEY_ID):
                        qbo_id = self._clean(found.get(const.QBO_KEY_ID))
                        qbo_token = self._clean(found.get(const.QBO_KEY_SYNC_TOKEN)) or None
                        existing_row = self.coa_repo.get_by_external_id(
                            tenant_id,
                            external_id=qbo_id,
                            service_id=str(service_id),
                            external_acc_id=str(realm_id),
                        )
                        if existing_row and int(existing_row.get("id") or 0) != int(account_id):
                            raise Exception(
                                f"QBO account Id {qbo_id} is already mapped to a different FIELDCRAFT account_id ({existing_row.get('id')})."
                            )
                        self.coa_repo.upsert_mapping(
                            tenant_id,
                            account_id=int(account_id),
                            external_id=qbo_id,
                            sync_token=qbo_token,
                            service_id=str(service_id),
                            external_acc_id=str(realm_id),
                        )
                        summary["mappedFromQbo"] += 1
                        detail[const.KEY_STATUS] = "success"
                        detail[const.KEY_ACTION] = "mappedFromQbo"
                        detail[const.KEY_QBO_ACCOUNT_ID] = qbo_id
                        detail[const.KEY_SYNC_TOKEN] = qbo_token
                        detail[const.KEY_REASON] = "Account already exists in QuickBooks; mapping updated"
                        summary[const.KEY_RESULTS].append(detail)
                        continue

                    created = self.create_account_in_qbo(
                        access_token=access_token,
                        base_url=base_url,
                        realm_id=str(realm_id),
                        payload=payload,
                    )
                    created_id = self._clean(created.get(const.QBO_KEY_ID))
                    created_token = self._clean(created.get(const.QBO_KEY_SYNC_TOKEN)) or None
                    if not created_id:
                        raise Exception(f"Create Account returned unexpected response: {created}")
                    self.coa_repo.upsert_mapping(
                        tenant_id,
                        account_id=int(account_id),
                        external_id=created_id,
                        sync_token=created_token,
                        service_id=str(service_id),
                        external_acc_id=str(realm_id),
                    )
                    summary["createdInQbo"] += 1
                    detail[const.KEY_STATUS] = "success"
                    detail[const.KEY_ACTION] = "createdInQbo"
                    detail[const.KEY_QBO_ACCOUNT_ID] = created_id
                    detail[const.KEY_SYNC_TOKEN] = created_token
                    detail[const.KEY_REASON] = "Created QuickBooks account and updated mapping"
                    summary[const.KEY_RESULTS].append(detail)
                except Exception as e:
                    logger.exception("[QBO-ACCOUNT-EXPORT] account processing failed | account_id=%s", account_id)
                    _intuit_tid = qbo_api.get_last_intuit_tid() if hasattr(qbo_api, "get_last_intuit_tid") else None
                    _err_str = str(e)
                    _is_invalid_account_type = self._is_qbo_account_type_error(_err_str)
                    if _is_invalid_account_type and not self._clean(account.get("external_id")):
                        try:
                            self.coa_repo.upsert_reason(
                                tenant_id,
                                account_id=int(account_id),
                                reason=self._build_reason(
                                    f"The account type '{account.get('account_type')}' is not available in QuickBooks. "
                                    "Please contact FIELDCRAFT to add this Chart of Account to QuickBooks.",
                                    intuit_tid=_intuit_tid,
                                ),
                            )
                        except Exception:
                            logger.warning(
                                "[QBO-ACCOUNT-EXPORT] failed to persist account rejection reason | account_id=%s",
                                account_id,
                                exc_info=True,
                            )
                    summary["failed"] += 1
                    detail[const.KEY_STATUS] = "failed"
                    detail[const.KEY_ACTION] = "error"
                    detail[const.KEY_ERROR] = _err_str
                    detail[const.KEY_REASON] = self._build_reason(
                        f"The account type '{account.get('account_type')}' is not available in QuickBooks. "
                        "Please contact FIELDCRAFT to add this Chart of Account to QuickBooks.",
                        intuit_tid=_intuit_tid,
                    ) if _is_invalid_account_type else None
                    summary[const.KEY_FAILURES].append({
                        const.KEY_FIELDCRAFT_ACCOUNT_ID: account_id,
                        const.KEY_ERROR: str(e),
                    })
                    summary[const.KEY_RESULTS].append(detail)
            logger.info(
                "[QBO-ACCOUNT-EXPORT] DONE ts=%s requested=%s skippedInvalid=%s skippedAlreadyCurrent=%s mappedFromQbo=%s createdInQbo=%s updatedInQbo=%s failed=%s",
                datetime.now(timezone.utc).isoformat(),
                summary.get('requested'),
                summary.get('skippedInvalid'),
                summary.get('skippedAlreadyCurrent'),
                summary.get('mappedFromQbo'),
                summary.get('createdInQbo'),
                summary.get('updatedInQbo'),
                summary.get('failed'),
            )
            _audit_action = "account_export_completed"
            _audit_status = "success"
            _audit_context = f"exported={summary.get('createdInQbo', 0) + summary.get('updatedInQbo', 0)} failed={summary.get('failed', 0)}"
            return summary
        except Exception as e:
            logger.exception("[QuickBooksAccountExportService.export_accounts] ERROR")
            _audit_action = "account_export_failed"
            _audit_status = "failed"
            _audit_context = str(e)[:64]
            return {const.KEY_OK: False, const.KEY_MESSAGE: "Account export failed.", const.KEY_ERROR: "Unexpected internal error. See server logs for details."}
        finally:
            # [AUDIT] Fires on every exit path — early return, success, or exception.
            safe_audit_log(
                tenant_id=tenant_id,
                action=_audit_action,
                user_id=object_id or None,
                email=email or None,
                user_role=user_role or None,
                status=_audit_status,
                context=_audit_context,
                resource_type="quickbooks_account",
            )

    def get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def refresh_access_token(
        self,
        *,
        tenant_id: str,
        service_id: str,
        connection: Optional[Dict[str, Any]],
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> str:
        return QuickBooksOAuthClient(repository=self.qbo_repo).refresh_access_token(
            tenant_id=str(tenant_id),
            service_id=str(service_id),
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=self.qbo_service.token_url,
            current_connection=connection,
        )

    def get_qbo_account_by_id(self, *, access_token: str, base_url: str, realm_id: str, account_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/account/{account_id}"
        resp = qbo_api.qbo_get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO get account failed"))
        data = qbo_api.safe_json(resp)
        return data.get("Account") or {}

    def find_account_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        name: str,
        acct_num: str,
        account_type: str,
        detail_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"

        def _first_account(resp_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            qry = (resp_json or {}).get("QueryResponse") or {}
            items = qry.get("Account") or []
            if not isinstance(items, list) or not items:
                return None
            for acc in items:
                if (
                    self._clean(acc.get("Name")) == self._clean(name)
                    and (not account_type or self._clean(acc.get("AccountType")) == self._clean(account_type))
                    and (not detail_type or self._clean(acc.get("AccountSubType")) == self._clean(detail_type))
                ):
                    return acc
            return items[0]

        if self._clean(acct_num):
            q = f"select Id, SyncToken, Name, AccountType, AccountSubType, AcctNum, Description, Active from Account where AcctNum = '{escape_qbo_literal(acct_num)}'"
            if account_type:
                q += f" and AccountType = '{escape_qbo_literal(account_type)}'"
            if detail_type:
                q += f" and AccountSubType = '{escape_qbo_literal(detail_type)}'"
            resp = qbo_api.qbo_get(query_url, headers=headers, params={"query": q}, timeout=30)
            if resp.status_code == 200:
                found = _first_account(qbo_api.safe_json(resp))
                if found:
                    return found

        q = f"select Id, SyncToken, Name, AccountType, AccountSubType, AcctNum, Description, Active from Account where Name = '{escape_qbo_literal(name)}'"
        if account_type:
            q += f" and AccountType = '{escape_qbo_literal(account_type)}'"
        if detail_type:
            q += f" and AccountSubType = '{escape_qbo_literal(detail_type)}'"
        resp = qbo_api.qbo_get(query_url, headers=headers, params={"query": q}, timeout=30)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO Account query failed"))
        return _first_account(qbo_api.safe_json(resp))

    def create_account_in_qbo(self, *, access_token: str, base_url: str, realm_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/account"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        resp = qbo_api.qbo_post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            raise Exception(qbo_api.build_response_error(resp, "Create Account failed"))
        data = qbo_api.safe_json(resp)
        return data.get("Account") or data

    def update_account_in_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        account_id: str,
        payload: Dict[str, Any],
        sync_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        token_value = self._clean(sync_token) or None
        if token_value:
            try:
                return self._post_account_update(
                    access_token=access_token,
                    base_url=base_url,
                    realm_id=realm_id,
                    account_id=account_id,
                    sync_token=token_value,
                    payload=payload,
                )
            except Exception as exc:
                if self._is_qbo_stale_reference_error(str(exc)):
                    raise
                if not self._is_qbo_sync_token_error(str(exc)):
                    raise
        existing = self.get_qbo_account_by_id(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            account_id=account_id,
        )
        current_sync_token = self._clean(existing.get(const.QBO_KEY_SYNC_TOKEN))
        if not current_sync_token:
            raise Exception("QBO account update failed: missing SyncToken")
        return self._post_account_update(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            account_id=account_id,
            sync_token=current_sync_token,
            payload=payload,
        )

    def _post_account_update(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        account_id: str,
        sync_token: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = f"{base_url}/v3/company/{realm_id}/account"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}
        body = {const.QBO_KEY_ID: str(account_id), const.QBO_KEY_SYNC_TOKEN: str(sync_token), "sparse": True, **(payload or {})}
        resp = qbo_api.qbo_post(url, headers=headers, json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise Exception(qbo_api.build_response_error(resp, "Update Account failed"))
        data = qbo_api.safe_json(resp)
        return data.get("Account") or data

    def build_qbo_account_payload(self, account: Dict[str, Any]) -> Dict[str, Any]:
        name = self._clean(account.get("name"))
        account_type = self._clean(account.get("account_type"))
        if not name:
            raise Exception("Missing account name")
        if not account_type:
            raise Exception("Missing account_type")
        payload: Dict[str, Any] = {
            "Name": name,
            "AccountType": account_type,
            "Active": False if account.get("active") is False else True,
        }
        detail_type = self._clean(account.get("detail_type"))
        if detail_type:
            payload["AccountSubType"] = detail_type
        acct_num = self._clean(account.get("account_no"))
        if acct_num:
            payload["AcctNum"] = acct_num
        desc = self._clean(account.get("description"))
        if desc:
            payload["Description"] = desc
        return payload

    def _qbo_account_matches_payload(self, qbo_account: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        def _same_bool(left: Any, right: Any) -> bool:
            return (False if left is False else True) == (False if right is False else True)
        return (
            self._clean(qbo_account.get("Name")) == self._clean(payload.get("Name"))
            and self._clean(qbo_account.get("AccountType")) == self._clean(payload.get("AccountType"))
            and self._clean(qbo_account.get("AccountSubType")) == self._clean(payload.get("AccountSubType"))
            and self._clean(qbo_account.get("AcctNum")) == self._clean(payload.get("AcctNum"))
            and self._clean(qbo_account.get("Description")) == self._clean(payload.get("Description"))
            and _same_bool(qbo_account.get("Active"), payload.get("Active"))
        )

    def _is_qbo_account_type_error(self, error_message: str) -> bool:
        error_text = str(error_message or "")
        error_text_lower = error_text.lower()
        return any(
            token in error_text_lower
            for token in (
                "accounttype",
                "accountsubtype",
                "account type",
                "account subtype",
            )
        )

    @staticmethod
    def _build_reason(message: str, intuit_tid: Optional[str] = None) -> str:
        data: Dict[str, Any] = {"message": message}
        if intuit_tid:
            data["intuit_tid"] = intuit_tid
        return json.dumps(data)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _escape(self, value: str) -> str:
        return escape_qbo_literal(value)

    def _is_qbo_stale_reference_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "[610]" in text or "object not found" in text or "made inactive" in text

    def _is_qbo_sync_token_error(self, message: str) -> bool:
        text = self._clean(message).lower()
        return "synctoken" in text or "stale object" in text or "[5010]" in text
