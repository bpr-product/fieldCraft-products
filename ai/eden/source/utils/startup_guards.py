import os

# SKIP_RBAC_CHECK=true bypasses page-level Postgres RBAC entirely (see
# rbac_resolver.py) -- fine for local dev where Postgres isn't reachable,
# but an easy thing to leave on by accident. main.py refuses to boot with
# it set outside a dev-labeled environment rather than silently running
# with RBAC off in production.
DEV_ENVIRONMENTS = {"development", "dev", "local"}


def check_rbac_bypass_guard(env: dict | None = None) -> None:
    """Raises RuntimeError if SKIP_RBAC_CHECK=true outside a dev
    environment. `env` defaults to os.environ; overridable for tests."""
    env = env if env is not None else os.environ
    app_env = env.get("APP_ENV", "development").strip().lower()
    skip_rbac = env.get("SKIP_RBAC_CHECK", "false").strip().lower() == "true"
    if skip_rbac and app_env not in DEV_ENVIRONMENTS:
        raise RuntimeError(
            f"SKIP_RBAC_CHECK=true is only permitted when APP_ENV is one of "
            f"{sorted(DEV_ENVIRONMENTS)} (got APP_ENV={app_env!r}). Page-level "
            "RBAC must never be bypassed outside local development -- set "
            "SKIP_RBAC_CHECK=false (or unset it) before deploying."
        )


def check_cors_wildcard_guard(env: dict | None = None) -> None:
    """Raises RuntimeError if ALLOWED_ORIGINS is "*" (or unset, which
    main.py's own default is also "*") outside a dev environment. Same
    reasoning/pattern as check_rbac_bypass_guard: a wildcard CORS origin is a
    reasonable local-dev default and an easy thing to leave on by accident --
    refuse to boot with it in a real environment rather than silently
    accepting cross-origin requests from anywhere."""
    env = env if env is not None else os.environ
    app_env = env.get("APP_ENV", "development").strip().lower()
    allowed_origins = env.get("ALLOWED_ORIGINS", "*").strip()
    if allowed_origins == "*" and app_env not in DEV_ENVIRONMENTS:
        raise RuntimeError(
            f"ALLOWED_ORIGINS=* is only permitted when APP_ENV is one of "
            f"{sorted(DEV_ENVIRONMENTS)} (got APP_ENV={app_env!r}). Set "
            "ALLOWED_ORIGINS to the actual frontend origin(s), comma-separated, "
            "before deploying anywhere else."
        )


MONGO_URI_PLACEHOLDER = "mongodb://localhost:27017"


def check_mongo_placeholder_guard(env: dict | None = None) -> None:
    """Raises RuntimeError outside a dev environment if Mongo would resolve
    to mongo_client.py's localhost fallback placeholder. Mirrors
    get_mongo_client()'s actual resolution chain (secrets.get_secret): Key
    Vault first if AZURE_KEYVAULT_NAME is configured (trusted to hold the
    real secret -- this guard isn't a substitute for a Key Vault outage
    check at request time), otherwise MONGODB_URI, otherwise the
    placeholder. Silently booting against the placeholder means every
    operational write (audit log, conversation memory) fails forever with
    no visible error -- same reasoning as the two guards above: refuse to
    boot instead."""
    env = env if env is not None else os.environ
    app_env = env.get("APP_ENV", "development").strip().lower()
    if app_env in DEV_ENVIRONMENTS:
        return
    keyvault_configured = bool(env.get("AZURE_KEYVAULT_NAME", "").strip())
    if keyvault_configured:
        return
    mongo_uri = env.get("MONGODB_URI", MONGO_URI_PLACEHOLDER).strip()
    if not mongo_uri or mongo_uri == MONGO_URI_PLACEHOLDER:
        raise RuntimeError(
            "MONGODB_URI resolves to the localhost placeholder outside a dev "
            f"environment (APP_ENV={app_env!r}), and no AZURE_KEYVAULT_NAME is "
            "configured to provide it instead. Set MONGODB_URI to the real "
            "cluster URI (or configure Key Vault) before deploying -- "
            "operational writes (audit log, conversation memory) fail "
            "silently against the placeholder."
        )
