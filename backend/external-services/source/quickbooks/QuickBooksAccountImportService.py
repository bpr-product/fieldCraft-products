import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from source.dal.ChartOfAccountRepository import ChartOfAccountRepository
from source.dal.QuickBooksRepository import QuickBooksRepository
from source.dal.ScheduledJobRepository import ScheduledJobRepository
from source.service.QuickBooksService import QuickBooksService
from source.Utils import constants as const
from source.quickbooks.QuickBooksAuthentication import QuickBooksOAuthClient
from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils.auditlog import safe_audit_log


logger = logging.getLogger(__name__)


class QuickBooksAccountImportService:
    """Import QuickBooks Online Accounts -> tenant DB chart_of_account.

    Import mode is controlled by the QBO_ACCOUNT_IMPORT_MODE environment variable:
      - CDC  (default): use last successful job start time as changedSince watermark;
                        fall back to full paging when no prior job exists.
      - FULL:           always run a full paginated import regardless of prior syncs.
    """

    def __init__(self):
        self.qbo_repo = QuickBooksRepository()
        self.qbo_service = QuickBooksService()
        self.coa_repo = ChartOfAccountRepository()
        self.scheduled_job_repo = ScheduledJobRepository()

    def import_accounts(
        self,
        *,
        tenant_id: str,
        service_id: str,
        page_size: int = 100,
        start_position: int = 1,
        max_accounts: Optional[int] = None,
        dry_run: bool = False,
        changed_since_override: Optional[Any] = None,
        object_id: Optional[str] = None,
        email: Optional[str] = None,
        user_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.info(
            "[QBO-ACCOUNT-IMPORT] START ts=%s tenant_id=%s service_id=%s page_size=%s start_position=%s max_accounts=%s dry_run=%s",
            datetime.now(timezone.utc).isoformat(),
            tenant_id,
            service_id,
            page_size,
            start_position,
            max_accounts,
            bool(dry_run),
        )
        safe_audit_log(
            tenant_id=tenant_id,
            action="account_import_started",
            user_id=object_id or None,
            email=email or None,
            user_role=user_role or None,
            status="started",
            resource_type="quickbooks_account",
        )
        # Track final outcome so the finally block logs it regardless of which exit path is taken.
        _audit_action = "account_import_failed"
        _audit_status = "failed"
        _audit_context: Optional[str] = None
        summary: Dict[str, Any] = {
            const.KEY_OK: True,
            "tenantId": tenant_id,
            "serviceId": service_id,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "finishedAt": None,
            "pageSize": int(page_size),
            "startPosition": int(start_position),
            "requestedMaxAccounts": int(max_accounts) if (max_accounts and int(max_accounts) > 0) else None,
            "dryRun": bool(dry_run),
            "mode": None,
            "changedSince": None,
            "watermarkSource": None,
            "processed": 0,
            "updatedInFieldCraft": 0,
            "linkedExistingInFieldCraft": 0,
            "createdInFieldCraft": 0,
            "skippedAlreadyCurrent": 0,
            "skippedLocalNewer": 0,
            "skippedUnavailable": 0,
            "failed": 0,
            const.KEY_FAILURES: [],
            const.KEY_RESULTS: [],
        }

        def _append_result(detail: Dict[str, Any]) -> None:
            st = (detail.get(const.KEY_STATUS) or "").strip().lower()
            if st in ("success", "failed", "skipped"):
                summary[const.KEY_RESULTS].append(detail)

        try:
            connection = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            refresh_token = connection.get(const.KEY_REFRESH_TOKEN)
            realm_id = connection.get(const.KEY_REALM_ID)
            env = (connection.get(const.KEY_ENV) or "sandbox").lower()
            conn_status = (connection.get(const.KEY_STATUS) or "").lower()
            summary[const.KEY_REALM_ID] = str(realm_id) if realm_id else None
            summary[const.KEY_ENV] = env

            if conn_status and conn_status != const.STATUS_CONNECTED:
                _audit_action, _audit_context = "account_import_aborted", f"not_connected status={conn_status}"
                return {**summary, const.KEY_OK: False, const.KEY_MESSAGE: f"QuickBooks is not connected (status={conn_status})"}
            if not refresh_token or not realm_id:
                _audit_action, _audit_context = "account_import_aborted", "missing_realmId_or_refreshToken"
                return {**summary, const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks not connected (missing refreshToken/realmId)."}

            config = self.qbo_service.get_quickbooks_config(tenant_id, service_id) or {}
            client_id = config.get(const.KEY_CLIENT_ID)
            client_secret = config.get(const.KEY_CLIENT_SECRET)
            if not client_id or not client_secret:
                _audit_action, _audit_context = "account_import_aborted", "missing_credentials"
                return {**summary, const.KEY_OK: False, const.KEY_MESSAGE: "QuickBooks client credentials missing (ClientId/ClientSecret)."}

            access_token = self._refresh_access_token(
                tenant_id=tenant_id,
                service_id=service_id,
                connection=connection,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            base_url = self._get_qbo_api_base(env)

            changed_since, watermark_source = self._resolve_changed_since(
                tenant_id=tenant_id,
                service_id=service_id,
                changed_since_override=changed_since_override,
            )
            use_cdc = changed_since is not None
            if use_cdc and self._is_cdc_too_old(changed_since):
                logger.warning("[QBO-ACCOUNT-IMPORT] CDC watermark too old (%s) -> FULL mode", changed_since)
                use_cdc = False
                changed_since = None
                watermark_source = None
            summary["mode"] = "CDC" if use_cdc else "FULL"
            summary["changedSince"] = changed_since.isoformat() if changed_since else None
            summary["watermarkSource"] = watermark_source
            logger.info("[QBO-ACCOUNT-IMPORT] Import mode=%s changedSince=%s watermarkSource=%s", summary['mode'], summary['changedSince'], summary['watermarkSource'])

            if use_cdc:
                batch, batch_intuit_tid = self._fetch_qbo_accounts_cdc(
                    base_url=base_url,
                    realm_id=str(realm_id),
                    access_token=access_token,
                    changed_since=changed_since,
                )
                if max_accounts is not None:
                    batch = batch[: int(max_accounts)]
                logger.info("[QBO-ACCOUNT-IMPORT] CDC mode - fetched %s accounts | intuit_tid=%s", len(batch), batch_intuit_tid)
                for acc in batch:
                    summary["processed"] += 1
                    self._process_qbo_account(
                        summary=summary,
                        tenant_id=tenant_id,
                        service_id=service_id,
                        realm_id=str(realm_id),
                        acc=acc,
                        intuit_tid=batch_intuit_tid,
                        dry_run=dry_run,
                    )
            else:
                processed = 0
                position = max(1, int(start_position))
                batch_size = int(page_size)
                while True:
                    if max_accounts is not None and processed >= int(max_accounts):
                        break
                    effective_size = batch_size
                    if max_accounts is not None:
                        effective_size = min(batch_size, int(max_accounts) - processed)
                    batch, batch_intuit_tid = self._query_qbo_accounts(
                        base_url=base_url,
                        realm_id=str(realm_id),
                        access_token=access_token,
                        start_position=position,
                        max_results=effective_size,
                    )
                    if not batch:
                        logger.info("[QBO-ACCOUNT-IMPORT] no more accounts from QBO, stopping.")
                        break
                    logger.info("[QBO-ACCOUNT-IMPORT] FULL mode - fetched %s accounts at position %s | intuit_tid=%s", len(batch), position, batch_intuit_tid)
                    for acc in batch:
                        processed += 1
                        summary["processed"] = processed
                        self._process_qbo_account(
                            summary=summary,
                            tenant_id=tenant_id,
                            service_id=service_id,
                            realm_id=str(realm_id),
                            acc=acc,
                            intuit_tid=batch_intuit_tid,
                            dry_run=dry_run,
                        )
                    position += len(batch)
                    if len(batch) < effective_size:
                        break

            _audit_action = "account_import_completed"
            _audit_status = "success"
            _audit_context = f"exported={summary.get('createdInFieldCraft', 0) + summary.get('updatedInFieldCraft', 0)} failed={summary.get('failed', 0)}"
            return summary
        except Exception as e:
            summary[const.KEY_OK] = False
            summary[const.KEY_MESSAGE] = "Account import failed."
            summary[const.KEY_ERROR] = "Unexpected internal error. See server logs for details."
            _audit_action = "account_import_failed"
            _audit_status = "failed"
            _audit_context = str(e)[:64]
            return summary
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
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

    def _process_qbo_account(
        self,
        *,
        summary: Dict[str, Any],
        tenant_id: str,
        service_id: str,
        realm_id: str,
        acc: Dict[str, Any],
        intuit_tid: Optional[str],
        dry_run: bool,
    ) -> None:
        qbo_account_id = self._clean(acc.get(const.QBO_KEY_ID))
        sync_token = self._clean(acc.get(const.QBO_KEY_SYNC_TOKEN)) or None
        name = self._clean(acc.get("Name"))
        qbo_account_type = self._clean(acc.get("AccountType"))
        qbo_account_sub_type = self._clean(acc.get("AccountSubType")) or None
        acct_num = self._clean(acc.get("AcctNum")) or None
        description = self._clean(acc.get("Description")) or None
        active_bool = False if acc.get("Active") is False else True
        currency_code = None
        cur_ref = acc.get("CurrencyRef") or {}
        if isinstance(cur_ref, dict):
            currency_code = self._clean(cur_ref.get("value") or cur_ref.get("name")) or None
        qbo_last_updated_at = self._get_qbo_last_updated_at(acc)

        detail: Dict[str, Any] = {
            const.KEY_QBO_ACCOUNT_ID: qbo_account_id or None,
            const.KEY_INTUIT_TID: intuit_tid,
            const.KEY_SYNC_TOKEN: sync_token,
            "name": name,
            "acctNum": acct_num,
            "qboAccountType": qbo_account_type or None,
            "qboAccountSubType": qbo_account_sub_type,
            "active": active_bool,
            "currencyCode": currency_code,
            const.KEY_STATUS: None,
            const.KEY_ACTION: None,
            const.KEY_FIELDCRAFT_ACCOUNT_ID: None,
            const.KEY_REASON: None,
            const.KEY_ERROR: None,
        }

        logger.info(
            "[QBO-ACCOUNT-IMPORT] Processing qboAccountId=%s name=%r acctNum=%r type=%s subType=%s active=%s qboLastUpdatedAt=%s",
            qbo_account_id,
            name,
            acct_num,
            qbo_account_type,
            qbo_account_sub_type,
            active_bool,
            qbo_last_updated_at,
        )
        try:
            if not qbo_account_id:
                raise Exception("Missing QBO Account.Id")
            if not name:
                raise Exception(f"Missing QBO Account.Name for Id={qbo_account_id}")
            if not qbo_account_type:
                raise Exception(f"Missing QBO Account.AccountType for Id={qbo_account_id}")

            remote_projection = {
                "name": name,
                "account_no": acct_num,
                "description": description,
                "active": active_bool,
                "currency_code": currency_code,
                "account_type": qbo_account_type,
                "detail_type": qbo_account_sub_type,
            }

            mapped = self.coa_repo.get_by_external_id_and_service(
                tenant_id,
                external_id=qbo_account_id,
                service_id=str(service_id),
            )
            logger.info("[QBO-ACCOUNT-IMPORT] qboAccountId=%s -> existing mapping lookup: %s", qbo_account_id, ('FOUND fieldcraftId=' + str(mapped.get('id'))) if mapped else 'NOT FOUND')
            if mapped:
                fieldcraft_id = int(mapped.get("id"))
                current = self.coa_repo.get_account_by_id(tenant_id, account_id=fieldcraft_id)
                local_latest = self.coa_repo.get_account_latest_local_timestamp(current)
                logger.info("[QBO-ACCOUNT-IMPORT] qboAccountId=%s fieldcraftId=%s localLatest=%s qboLastUpdated=%s", qbo_account_id, fieldcraft_id, local_latest, qbo_last_updated_at)
                if current and not self.coa_repo.sync_projection_changed(current, remote_projection):
                    if not dry_run:
                        self.coa_repo.upsert_mapping(
                            tenant_id,
                            account_id=fieldcraft_id,
                            external_id=qbo_account_id,
                            sync_token=sync_token,
                            service_id=str(service_id),
                            external_acc_id=str(realm_id),
                        )
                    summary["skippedAlreadyCurrent"] += 1
                    detail[const.KEY_STATUS] = "skipped"
                    detail[const.KEY_ACTION] = "alreadyCurrent"
                    logger.info("[QBO-ACCOUNT-IMPORT] SKIP alreadyCurrent qboAccountId=%s fieldcraftId=%s", qbo_account_id, fieldcraft_id)
                    detail[const.KEY_FIELDCRAFT_ACCOUNT_ID] = fieldcraft_id
                    detail[const.KEY_REASON] = "Account already matches QuickBooks"
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if self._should_skip_import_update(qbo_last_updated_at, local_latest):
                    if not dry_run:
                        self.coa_repo.upsert_mapping(
                            tenant_id,
                            account_id=fieldcraft_id,
                            external_id=qbo_account_id,
                            sync_token=sync_token,
                            service_id=str(service_id),
                            external_acc_id=str(realm_id),
                        )
                    summary["skippedLocalNewer"] += 1
                    detail[const.KEY_STATUS] = "skipped"
                    detail[const.KEY_ACTION] = "localNewer"
                    detail[const.KEY_FIELDCRAFT_ACCOUNT_ID] = fieldcraft_id
                    detail[const.KEY_REASON] = "Skipped import because local FIELDCRAFT account is newer than QuickBooks"
                    logger.info("[QBO-ACCOUNT-IMPORT] SKIP localNewer qboAccountId=%s fieldcraftId=%s localLatest=%s qboLastUpdated=%s", qbo_account_id, fieldcraft_id, local_latest, qbo_last_updated_at)
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if not dry_run:
                    self.coa_repo.update_account_safe_fields_from_qbo(
                        tenant_id=tenant_id,
                        account_id=fieldcraft_id,
                        name=name,
                        account_no=acct_num,
                        description=description,
                        active=active_bool,
                        currency_code=currency_code,
                        account_type=qbo_account_type,
                        detail_type=qbo_account_sub_type,
                        source_updated_at=qbo_last_updated_at,
                    )
                    self.coa_repo.upsert_mapping(
                        tenant_id,
                        account_id=fieldcraft_id,
                        external_id=qbo_account_id,
                        sync_token=sync_token,
                        service_id=str(service_id),
                        external_acc_id=str(realm_id),
                    )
                summary["updatedInFieldCraft"] += 1
                detail[const.KEY_STATUS] = "success"
                detail[const.KEY_ACTION] = "updatedExistingMapped"
                logger.info("[QBO-ACCOUNT-IMPORT] UPDATE mappedAccount qboAccountId=%s fieldcraftId=%s dry_run=%s", qbo_account_id, fieldcraft_id, dry_run)
                detail[const.KEY_FIELDCRAFT_ACCOUNT_ID] = fieldcraft_id
                detail[const.KEY_REASON] = "Account already mapped; updated fields + sync token"
                summary[const.KEY_RESULTS].append(detail)
                return

            logger.info("[QBO-ACCOUNT-IMPORT] qboAccountId=%s no existing mapping, searching local match by acctNum=%r name=%r", qbo_account_id, acct_num, name)
            existing = self._find_best_existing_local_match(
                tenant_id=tenant_id,
                name=name,
                acct_num=acct_num,
                qbo_account_type=qbo_account_type,
                qbo_account_sub_type=qbo_account_sub_type,
            )
            logger.info("[QBO-ACCOUNT-IMPORT] qboAccountId=%s local match: %s", qbo_account_id, ('FOUND fieldcraftId=' + str(existing.get('id'))) if existing else 'NOT FOUND -> will CREATE')
            if existing:
                fieldcraft_id = int(existing.get("id"))
                existing_external_id = self._clean(existing.get("external_id"))
                existing_service = self._clean(existing.get("service_id"))
                if existing_external_id and existing_external_id != qbo_account_id and existing_service == str(service_id):
                    raise Exception(
                        f"FIELDCRAFT account_id={fieldcraft_id} is already mapped to QBO Id={existing_external_id}; cannot remap to {qbo_account_id}."
                    )
                current = self.coa_repo.get_account_by_id(tenant_id, account_id=fieldcraft_id)
                local_latest = self.coa_repo.get_account_latest_local_timestamp(current)
                if self._should_skip_import_update(qbo_last_updated_at, local_latest) and current and self.coa_repo.sync_projection_changed(current, remote_projection):
                    if not dry_run:
                        self.coa_repo.upsert_mapping(
                            tenant_id=tenant_id,
                            account_id=fieldcraft_id,
                            external_id=qbo_account_id,
                            sync_token=sync_token,
                            service_id=str(service_id),
                            external_acc_id=str(realm_id),
                        )
                    summary["linkedExistingInFieldCraft"] += 1
                    detail[const.KEY_STATUS] = "success"
                    detail[const.KEY_ACTION] = "linkedExistingLocalNewer"
                    detail[const.KEY_FIELDCRAFT_ACCOUNT_ID] = fieldcraft_id
                    detail[const.KEY_REASON] = "Linked existing chart_of_account and preserved newer local data"
                    logger.info("[QBO-ACCOUNT-IMPORT] LINK linkedExistingLocalNewer qboAccountId=%s fieldcraftId=%s dry_run=%s", qbo_account_id, fieldcraft_id, dry_run)
                    summary[const.KEY_RESULTS].append(detail)
                    return
                if not dry_run:
                    self.coa_repo.update_account_safe_fields_from_qbo(
                        tenant_id=tenant_id,
                        account_id=fieldcraft_id,
                        name=name,
                        account_no=acct_num,
                        description=description,
                        active=active_bool,
                        currency_code=currency_code,
                        account_type=qbo_account_type,
                        detail_type=qbo_account_sub_type,
                        source_updated_at=qbo_last_updated_at,
                    )
                    self.coa_repo.upsert_mapping(
                        tenant_id=tenant_id,
                        account_id=fieldcraft_id,
                        external_id=qbo_account_id,
                        sync_token=sync_token,
                        service_id=str(service_id),
                        external_acc_id=str(realm_id),
                    )
                summary["linkedExistingInFieldCraft"] += 1
                detail[const.KEY_STATUS] = "success"
                detail[const.KEY_ACTION] = "linkedExistingInFieldCraft"
                logger.info("[QBO-ACCOUNT-IMPORT] LINK linkedExistingInFieldCraft qboAccountId=%s fieldcraftId=%s dry_run=%s", qbo_account_id, fieldcraft_id, dry_run)
                detail[const.KEY_FIELDCRAFT_ACCOUNT_ID] = fieldcraft_id
                detail[const.KEY_REASON] = "Matched existing chart_of_account by account_no/name; linked + updated"
                summary[const.KEY_RESULTS].append(detail)
                return

            new_account_no = acct_num or f"{const.QBO_ACCOUNT}{qbo_account_id}"
            logger.info("[QBO-ACCOUNT-IMPORT] CREATE new account qboAccountId=%s name=%r new_account_no=%r dry_run=%s", qbo_account_id, name, new_account_no, dry_run)
            new_id = 0
            if not dry_run:
                new_id = self.coa_repo.insert_account_from_qbo(
                    tenant_id=tenant_id,
                    name=name,
                    account_no=new_account_no,
                    qbo_account_type=qbo_account_type,
                    qbo_account_sub_type=qbo_account_sub_type,
                    description=description,
                    active=active_bool,
                    currency_code=currency_code,
                    external_id=qbo_account_id,
                    sync_token=sync_token,
                    service_id=str(service_id),
                    external_acc_id=str(realm_id),
                    source_updated_at=qbo_last_updated_at,
                )
            summary["createdInFieldCraft"] += 1
            detail[const.KEY_STATUS] = "success"
            detail[const.KEY_ACTION] = "createdInFieldCraft"
            detail[const.KEY_FIELDCRAFT_ACCOUNT_ID] = int(new_id) if new_id else None
            detail[const.KEY_REASON] = "Created new chart_of_account row and mapped to QBO"
            summary[const.KEY_RESULTS].append(detail)
        except Exception as e:
            summary["failed"] += 1
            detail[const.KEY_STATUS] = "failed"
            detail[const.KEY_ACTION] = "error"
            detail[const.KEY_ERROR] = str(e)
            summary[const.KEY_FAILURES].append({
                const.KEY_QBO_ACCOUNT_ID: qbo_account_id or None,
                const.KEY_ERROR: str(e),
            })
            summary[const.KEY_RESULTS].append(detail)
            logger.error("[QBO-ACCOUNT-IMPORT] FAILED qboAccountId=%s error=%s", qbo_account_id, e)
            logger.exception("[QBO-ACCOUNT-IMPORT] account processing failed")

    def _find_best_existing_local_match(
        self,
        *,
        tenant_id: str,
        name: str,
        acct_num: Optional[str],
        qbo_account_type: str,
        qbo_account_sub_type: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if acct_num:
            existing_by_no = self.coa_repo.get_by_account_no(tenant_id, account_no=acct_num)
            if existing_by_no and not bool(existing_by_no.get("invalid")):
                logger.info("[QBO-ACCOUNT-IMPORT] _find_best_existing_local_match: matched by acctNum=%r -> fieldcraftId=%s", acct_num, existing_by_no.get('id'))
                return existing_by_no
        candidates = self.coa_repo.list_by_name(tenant_id, name=name, limit=50)
        logger.info("[QBO-ACCOUNT-IMPORT] _find_best_existing_local_match: name search name=%r -> %s candidate(s)", name, len(candidates))
        for candidate in candidates:
            if bool(candidate.get("invalid")):
                continue
            if self._clean(candidate.get("account_type")) != self._clean(qbo_account_type):
                continue
            if self._clean(candidate.get("detail_type")) != self._clean(qbo_account_sub_type):
                continue
            logger.info(
                "[QBO-ACCOUNT-IMPORT] _find_best_existing_local_match: matched by name+db type fieldcraftId=%s accountType=%s detailType=%s",
                candidate.get('id'),
                candidate.get('account_type'),
                candidate.get('detail_type'),
            )
            return candidate
        for candidate in candidates:
            if bool(candidate.get("invalid")):
                continue
            logger.info("[QBO-ACCOUNT-IMPORT] _find_best_existing_local_match: matched by name fallback fieldcraftId=%s", candidate.get('id'))
            return candidate
        logger.info("[QBO-ACCOUNT-IMPORT] _find_best_existing_local_match: no local match found for name=%r acctNum=%r", name, acct_num)
        return None

    def _normalize_changed_since(self, changed_since: Optional[datetime]) -> Optional[datetime]:
        if changed_since is None:
            return None
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        return changed_since - timedelta(minutes=1)

    def _get_account_module_changed_since(self, tenant_id: str, service_id: str) -> Optional[datetime]:
        try:
            last_sync_start = self.scheduled_job_repo.get_last_successful_module_sync_start(
                tenant_id=tenant_id,
                job_type="QBO_ACCOUNT_SYNC",
                service_id=str(service_id),
            )
            return self._normalize_changed_since(last_sync_start)
        except Exception:
            return None

    def _get_account_manual_changed_since(self, tenant_id: str, service_id: str) -> Optional[datetime]:
        try:
            additional_info = self.qbo_repo.getAdditionalInfo(tenant_id, service_id) or {}
            return self._normalize_changed_since(self._parse_timestamp(additional_info.get("lastAccountSyncAt")))
        except Exception:
            return None

    def _resolve_changed_since(self, tenant_id: str, service_id: str, changed_since_override: Optional[Any] = None) -> Tuple[Optional[datetime], Optional[str]]:
        import_mode = os.getenv("QBO_ACCOUNT_IMPORT_MODE", "FULL").upper().strip()
        logger.info("[QBO-ACCOUNT-IMPORT] QBO_ACCOUNT_IMPORT_MODE=%s", import_mode)
        if import_mode == "FULL":
            logger.info("[QBO-ACCOUNT-IMPORT] Forced FULL import - skipping CDC watermark lookup")
            return None, None
        module_changed_since = self._get_account_module_changed_since(tenant_id=tenant_id, service_id=service_id)
        if module_changed_since is not None:
            return module_changed_since, "scheduledJob"
        logger.info("[QBO-ACCOUNT-IMPORT] No prior job found - falling back to FULL import")
        return None, None

    def _is_cdc_too_old(self, changed_since: datetime) -> bool:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        return changed_since < (datetime.now(timezone.utc) - timedelta(days=29))

    def _fetch_qbo_accounts_cdc(
        self,
        *,
        base_url: str,
        realm_id: str,
        access_token: str,
        changed_since: datetime,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        changed_since_value = changed_since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        url = f"{base_url}/v3/company/{realm_id}/cdc"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "text/plain"}
        params = {"entities": "Account", "changedSince": changed_since_value}
        resp = qbo_api.qbo_get(url, headers=headers, params=params, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO account CDC failed"))
        data = qbo_api.safe_json(resp)
        items_by_id: Dict[str, Dict[str, Any]] = {}
        for cdc_block in data.get("CDCResponse") or []:
            for query_response in cdc_block.get("QueryResponse") or []:
                for account in query_response.get("Account") or []:
                    account_id = self._clean(account.get(const.QBO_KEY_ID))
                    if account_id:
                        items_by_id[account_id] = account
        return list(items_by_id.values()), qbo_api.extract_intuit_tid(resp)

    def get_qbo_account_by_id(self, *, base_url: str, realm_id: str, access_token: str, account_id: str) -> Optional[Dict[str, Any]]:
        url = f"{base_url}/v3/company/{realm_id}/account/{account_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        resp = qbo_api.qbo_get(url, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO account fetch failed"))
        data = qbo_api.safe_json(resp)
        if isinstance(data, dict):
            acc = data.get("Account")
            if isinstance(acc, dict):
                return acc
        return None

    def _get_qbo_api_base(self, env: str) -> str:
        from source.Utils import QBODiscoveryClient
        return QBODiscoveryClient.get_qbo_api_base(env)

    def _refresh_access_token(
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

    def _query_qbo_accounts(
        self,
        *,
        base_url: str,
        realm_id: str,
        access_token: str,
        start_position: int,
        max_results: int,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        query_url = f"{base_url}/v3/company/{realm_id}/query"
        query = f"select * from Account STARTPOSITION {int(start_position)} MAXRESULTS {int(max_results)}"
        resp = qbo_api.qbo_get(query_url, headers=headers, params={"query": query}, timeout=30)
        if resp.status_code != 200:
            raise Exception(qbo_api.build_response_error(resp, "QBO account query failed"))
        data = qbo_api.safe_json(resp)
        accounts = (data.get("QueryResponse") or {}).get("Account") or []
        return (accounts if isinstance(accounts, list) else [], qbo_api.extract_intuit_tid(resp))

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            if not raw:
                return None
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _get_qbo_last_updated_at(self, account: Dict[str, Any]) -> Optional[datetime]:
        meta = account.get("MetaData") or {}
        return self._parse_timestamp(meta.get("LastUpdatedTime") or meta.get("CreateTime"))

    def _should_skip_import_update(self, qbo_last_updated_at: Optional[datetime], local_latest_at: Optional[datetime]) -> bool:
        if qbo_last_updated_at is None or local_latest_at is None:
            return False
        return qbo_last_updated_at <= local_latest_at

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()
