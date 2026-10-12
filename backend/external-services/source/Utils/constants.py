"""
NOTE: Values here are part of the public contract between services and the UI.
Do not change them unless you also update all producers/consumers.
"""

KEY_ACTION = "action"
KEY_STATUS = "status"

KEY_EXTERNAL_ID = "external_id"
KEY_EXTERNAL_ID_CAMEL = "externalId"

KEY_QBO_VENDOR_ID = "qboVendorId"
KEY_QBO_CUSTOMER_ID = "qboCustomerId"
KEY_QBO_ACCOUNT_ID = "qboAccountId"

KEY_QBO_PAYMENT_ID = "qboPaymentId"
KEY_QBO_EMPLOYEE_ID = "qboEmployeeId"

KEY_FIELDCRAFT_VENDOR_ID = "fieldcraftVendorId"
KEY_FIELDCRAFT_CUSTOMER_ID = "fieldcraftCustomerId"
KEY_FIELDCRAFT_ACCOUNT_ID = "fieldcraftAccountId"

KEY_FIELDCRAFT_PAYMENT_ID = "fieldcraftPaymentId"
KEY_FIELDCRAFT_EMPLOYEE_ID = "fieldcraftEmployeeId"

KEY_VENDOR_ID = "vendor_id"
KEY_VENDOR_ID_CAMEL = "vendorId"

KEY_ACCOUNT_ID = "account_id"
KEY_ACCOUNT_ID_CAMEL = "accountId"

KEY_CUSTOMER_ID = "customer_id"
KEY_CUSTOMER_ID_CAMEL = "customerId"

KEY_ACCOUNT_NO = "account_no"
KEY_ACCOUNT_TYPE = "account_type"
KEY_DETAIL_TYPE = "detail_type"

KEY_DISPLAY_NAME = "displayName"
KEY_VENDOR_NAME = "vendorName"
KEY_CUSTOMER_NAME = "customerName"
KEY_NAME = "name"

KEY_EMAIL = "email"
KEY_PHONE = "phone"
KEY_REASON = "reason"
KEY_ERROR = "error"
KEY_MESSAGE = "message"

KEY_INTUIT_TID = "intuitTid"
HEADER_INTUIT_TID = "intuit_tid"
KEY_STATUS_CODE = "statusCode"
KEY_ENDPOINT = "endpoint"
KEY_DETAILS = "details"
KEY_RESPONSE_BODY = "responseBody"


# Connection/config keys
KEY_REFRESH_TOKEN = "refreshToken"
KEY_ACCESS_TOKEN = "accessToken"
KEY_REALM_ID = "realmId"
KEY_ENV = "env"
KEY_CLIENT_ID = "ClientId"
KEY_CLIENT_SECRET = "ClientSecret"
KEY_PROJECT_CLIENT_REALM_ID = "projectClientRealmId"
KEY_QBO_CLIENT_ENTITY_ID = "ClientEntityId"

# QuickBooks Time uses a separate OAuth application and stores its connection
# state in the same service-subscriber additional_info document.
KEY_TIME_CLIENT_ID = "TimeClientId"
KEY_TIME_CLIENT_SECRET = "TimeClientSecret"
KEY_TIME_STATE = "timeState"
KEY_TIME_STATE_EXPIRES_AT = "timeStateExpiresAt"
KEY_TIME_STATE_USED_AT = "timeStateUsedAt"
KEY_TIME_COMPANY_ID = "timeCompanyId"
KEY_TIME_USER_ID = "timeUserId"
KEY_TIME_REFRESH_TOKEN = "timeRefreshToken"
KEY_TIME_ACCESS_TOKEN = "timeAccessToken"
KEY_TIME_ACCESS_EXPIRES_AT = "timeAccessExpiresAt"
KEY_TIME_STATUS = "timeStatus"
KEY_TIME_RECONNECT_REQUIRED = "timeReconnectRequired"
KEY_TIME_AUTH_ERROR = "timeAuthError"
KEY_TIME_CONNECTED_AT = "timeConnectedAt"

KEY_RESULTS = "results"
KEY_FAILURES = "failures"
KEY_OK = "ok"

KEY_SYNC_TOKEN = "syncToken"
KEY_SYNC_TOKEN_SNAKE = "sync_token"

# QuickBooks (QBO) API object keys
QBO_KEY_ID = "Id"
QBO_KEY_SYNC_TOKEN = "SyncToken"
QBO_KEY_DISPLAY_NAME = "DisplayName"
VENDOR_DEFAULT_TYPE = "otherVendors"

# QuickBooks DisplayName convention
QBO_ENTITY_TYPE_CUSTOMER = "customer"
QBO_ENTITY_TYPE_VENDOR = "vendor"
QBO_ENTITY_TYPE_EMPLOYEE = "employee"
QBO_DISPLAY_SHORT_CODE_LENGTH = 6

# Scheduled job statuses
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_COMPLETED = "COMPLETED"
JOB_STATUS_COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
JOB_STATUS_FAILED = "FAILED"

#Quickbooks Bills object keys
KEY_QBO_BILL_ID = "qboBillId"
KEY_QBO_JOURNAL_ENTRY_ID = "qboJournalEntryId"
KEY_QBO_ENTITY_TYPE = "qboEntityType"
KEY_FIELDCRAFT_BILL_ID = "fieldcraftBillId"
QBO_RECEIPT_ENTITY_TYPE_BILL = "BILL"
QBO_RECEIPT_ENTITY_TYPE_JOURNAL_ENTRY = "JOURNAL_ENTRY"
QBO_RECEIPT_ENTITY_TYPES_ALLOWED = (
    QBO_RECEIPT_ENTITY_TYPE_BILL,
    QBO_RECEIPT_ENTITY_TYPE_JOURNAL_ENTRY,
)
# Bill export configuration / constants
ENV_QBO_BILL_SYNC_ENABLED = "QBO_BILL_SYNC_ENABLED"
ENV_QBO_BILL_EXPORT_BATCH_SIZE = "QBO_BILL_EXPORT_BATCH_SIZE"
ENV_QBO_BILL_EXPORT_START_OFFSET = "QBO_BILL_EXPORT_START_OFFSET"
ENV_QBO_BILL_RECEIPT_NAMES = "QBO_BILL_RECEIPT_NAMES"

QBO_BILL_DEFAULT_BATCH_SIZE = 200
QBO_BILL_DEFAULT_START_OFFSET = 0
QBO_BILL_DEFAULT_RECEIPT_NAMES = "Bill,VendorBill"
QBO_STOCK_DEBIT_BILL_NAME = "StockDebitBill"
QBO_STOCK_CREDIT_BILL_NAME = "StockCreditBill"
QBO_INVENTORY_ASSET_ACCOUNT_NAME = "Inventory Asset"
QBO_INVENTORY_ASSET_ACCOUNT_TYPES = ("Other Current Asset", "OtherCurrentAsset")
QBO_INVENTORY_ASSET_DETAIL_TYPES = ("Inventory",)

SERVICE_NAME_QUICKBOOKS = "QuickBooks"

ENV_QBO_VENDOR_DEFAULT_SERVICES = "QBO_VENDOR_DEFAULT_SERVICES"
QBO_VENDOR_DEFAULT_SERVICES = "otherVendors"


# QuickBooks connection status / auth handling
STATUS_CONNECTED = "connected"
STATUS_DISCONNECTED = "disconnected"
STATUS_REAUTH_REQUIRED = "reauthorization_required"

KEY_RECONNECT_REQUIRED = "reconnectRequired"
KEY_AUTH_ERROR = "authError"
KEY_REFRESH_EXPIRES_AT = "refreshExpiresAt"
KEY_ACCESS_EXPIRES_AT = "accessExpiresAt"

ENV_QBO_CONNECTION_ENCRYPTION_CONTEXT = "QBO_CONNECTION_ENCRYPTION_CONTEXT"
DEFAULT_QBO_CONNECTION_ENCRYPTION_CONTEXT = "QuickBooks.connection"

AUTH_ERROR_ACCESS_TOKEN_EXPIRED = "access_token_expired"
AUTH_ERROR_REFRESH_TOKEN_EXPIRED = "refresh_token_expired"
AUTH_ERROR_INVALID_GRANT = "invalid_grant"
AUTH_ERROR_CSRF = "csrf_validation_failed"

QBO_ACCOUNT = "acct-"

# QuickBooks subscription capability keys — stored in additional_info["capabilities"].
# NOTE: Values here are part of the public contract between services and the UI.
KEY_CAPABILITIES = "capabilities"
CAPABILITY_BILLS = "bills_enabled"
CAPABILITY_PROJECTS = "projects_enabled"
CAPABILITY_INVENTORY = "inventory_enabled"
CAPABILITY_TIME_TRACKING = "time_tracking_enabled"

# Tenant-level automatic sync configuration — stored in additional_info["auto_sync"].
# Existing tenants default to enabled when the setting has not been saved yet.
KEY_AUTO_SYNC = "auto_sync"
KEY_AUTO_SYNC_ENABLED = "enabled"
DEFAULT_AUTO_SYNC_ENABLED = False

# Project sync configuration — stored in additional_info["project_sync"].
# AUTO lets the system use real QuickBooks Projects when available and fall back
# to sub-customers. Existing mapped projects keep their original target.
KEY_PROJECT_SYNC = "project_sync"
KEY_PROJECT_SYNC_METHOD = "method"
KEY_PROJECTS_AVAILABLE = "projects_available"
KEY_PROJECT_SYNC_TARGET = "project_sync_target"
KEY_EFFECTIVE_PROJECT_SYNC_TARGET = "effective_project_sync_target"
PROJECT_SYNC_METHOD_AUTO = "AUTO"
PROJECT_SYNC_METHOD_QBO_PROJECT = "QBO_PROJECT"
PROJECT_SYNC_METHOD_SUB_CUSTOMER = "SUB_CUSTOMER"
PROJECT_SYNC_METHODS_ALLOWED = (
    PROJECT_SYNC_METHOD_AUTO,
    PROJECT_SYNC_METHOD_QBO_PROJECT,
    PROJECT_SYNC_METHOD_SUB_CUSTOMER,
)
PROJECT_SYNC_TARGET_QBO_PROJECT = "QBO_PROJECT"
PROJECT_SYNC_TARGET_SUB_CUSTOMER = "SUB_CUSTOMER"
PROJECT_SYNC_TARGETS_ALLOWED = (PROJECT_SYNC_TARGET_QBO_PROJECT, PROJECT_SYNC_TARGET_SUB_CUSTOMER)
PROJECTS_NOT_AVAILABLE_MESSAGE = (
    "QuickBooks Projects are not enabled for this company. "
    "Choose Auto if you are not sure, or choose Sub-customer to continue syncing projects."
)

# Employee sync configuration — stored in additional_info["employee_sync"].
# NOTE: Values here are part of the public contract between services and the UI.
# Do not rename these keys without updating all producers/consumers.
KEY_EMPLOYEE_SYNC = "employee_sync"
KEY_EMPLOYEE_SYNC_DAYS_AFTER_HIRE = "days_after_hire"
KEY_EMPLOYEE_SYNC_TYPE = "employee_type"
KEY_EMPLOYEE_SYNC_COST_ENABLED = "cost_enabled"
KEY_EMPLOYEE_SYNC_DEFAULT_COST_UNIT = "default_cost_unit"
KEY_EMPLOYEE_SYNC_COST_UNIT_HOURS = "cost_unit_hours"
KEY_EMPLOYEE_SYNC_COST_QBO_FIELD = "cost_qbo_field"

# Allowed employee payroll types.
# QBO Employee entity represents W-2 workers; 1099 contractors live in QBO as Vendors.
# Default is W2, but admins can override per-tenant when their QBO setup differs.
EMPLOYEE_TYPE_W2 = "W2"
EMPLOYEE_TYPE_1099 = "1099"
EMPLOYEE_TYPE_SUBCONTRACTOR = "Subcontractor"
EMPLOYEE_TYPE_CONTRACTOR = "Contractor"
EMPLOYEE_TYPES_ALLOWED = (EMPLOYEE_TYPE_W2, EMPLOYEE_TYPE_1099)
CONTRACTOR_RESOURCE_TYPES = (
    EMPLOYEE_TYPE_1099,
    EMPLOYEE_TYPE_SUBCONTRACTOR,
    EMPLOYEE_TYPE_CONTRACTOR,
)

# External entity type stored on user_account_external_map.
# W2 resources map to QBO Employee; 1099/contractor resources map to QBO Vendor.
WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE = "EMPLOYEE"
WORKER_EXTERNAL_ENTITY_TYPE_VENDOR = "VENDOR"
WORKER_EXTERNAL_ENTITY_TYPES_ALLOWED = (
    WORKER_EXTERNAL_ENTITY_TYPE_EMPLOYEE,
    WORKER_EXTERNAL_ENTITY_TYPE_VENDOR,
)
KEY_QBO_WORKER_ENTITY_TYPE = "qboWorkerEntityType"
KEY_QBO_WORKER_ID = "qboWorkerId"

# Allowed range for days_after_hire offset.
# Min = 1: work must start at least 1 day after hire (same-day not allowed).
# Max = 365: prevents unreasonable future-dating.
EMPLOYEE_SYNC_DAYS_MIN = 1
EMPLOYEE_SYNC_DAYS_MAX = 365

# Employee billing-cost sync configuration.
EMPLOYEE_COST_UNIT_HOURLY = "hourly"
EMPLOYEE_COST_UNIT_DAILY = "daily"
EMPLOYEE_COST_UNIT_WEEKLY = "weekly"
EMPLOYEE_COST_UNIT_MONTHLY = "monthly"
EMPLOYEE_COST_UNIT_YEARLY = "yearly"
EMPLOYEE_COST_UNITS_ALLOWED = (
    EMPLOYEE_COST_UNIT_HOURLY,
    EMPLOYEE_COST_UNIT_DAILY,
    EMPLOYEE_COST_UNIT_WEEKLY,
    EMPLOYEE_COST_UNIT_MONTHLY,
    EMPLOYEE_COST_UNIT_YEARLY,
)
EMPLOYEE_COST_UNIT_HOURS_DEFAULT = {
    EMPLOYEE_COST_UNIT_HOURLY: 1,
    EMPLOYEE_COST_UNIT_DAILY: 8,
    EMPLOYEE_COST_UNIT_WEEKLY: 40,
    EMPLOYEE_COST_UNIT_MONTHLY: 160,
    EMPLOYEE_COST_UNIT_YEARLY: 2080,
}
EMPLOYEE_COST_QBO_FIELD_COST_RATE = "CostRate"
EMPLOYEE_COST_QBO_FIELD_BILL_RATE = "BillRate"
EMPLOYEE_COST_QBO_FIELDS_ALLOWED = (
    EMPLOYEE_COST_QBO_FIELD_COST_RATE,
    EMPLOYEE_COST_QBO_FIELD_BILL_RATE,
)

# Public API batch limits. Bounds protect the tenant DB and QBO rate limit while
# keeping existing defaults unchanged.
QBO_EXPORT_LIMIT_MAX = 1000
QBO_IMPORT_PAGE_SIZE_MAX = 1000
QBO_IMPORT_REQUESTED_MAX = 10000
