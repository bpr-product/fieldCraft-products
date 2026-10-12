"""Unit tests for the RBAC-bypass, CORS-wildcard, and Mongo-placeholder
production guards -- SKIP_RBAC_CHECK=true, ALLOWED_ORIGINS=*, and an
unresolved MONGODB_URI must never silently run outside local development."""
import pytest

from source.utils.startup_guards import (
    check_cors_wildcard_guard,
    check_mongo_placeholder_guard,
    check_rbac_bypass_guard,
)


def test_allows_skip_rbac_in_development():
    check_rbac_bypass_guard({"SKIP_RBAC_CHECK": "true", "APP_ENV": "development"})


def test_allows_skip_rbac_in_dev_alias():
    check_rbac_bypass_guard({"SKIP_RBAC_CHECK": "true", "APP_ENV": "dev"})


def test_allows_skip_rbac_in_local_alias():
    check_rbac_bypass_guard({"SKIP_RBAC_CHECK": "true", "APP_ENV": "local"})


def test_allows_no_skip_rbac_in_production():
    check_rbac_bypass_guard({"SKIP_RBAC_CHECK": "false", "APP_ENV": "production"})


def test_defaults_to_development_when_app_env_unset():
    check_rbac_bypass_guard({"SKIP_RBAC_CHECK": "true"})


def test_raises_when_skip_rbac_true_in_production():
    with pytest.raises(RuntimeError, match="SKIP_RBAC_CHECK"):
        check_rbac_bypass_guard({"SKIP_RBAC_CHECK": "true", "APP_ENV": "production"})


def test_raises_when_skip_rbac_true_and_app_env_unset_but_overridden_elsewhere():
    with pytest.raises(RuntimeError):
        check_rbac_bypass_guard({"SKIP_RBAC_CHECK": "true", "APP_ENV": "staging"})


def test_case_and_whitespace_insensitive():
    check_rbac_bypass_guard({"SKIP_RBAC_CHECK": "TRUE", "APP_ENV": "  Development  "})
    with pytest.raises(RuntimeError):
        check_rbac_bypass_guard({"SKIP_RBAC_CHECK": " True ", "APP_ENV": "Production"})


def test_allows_cors_wildcard_in_development():
    check_cors_wildcard_guard({"ALLOWED_ORIGINS": "*", "APP_ENV": "development"})


def test_allows_cors_wildcard_when_app_env_unset():
    check_cors_wildcard_guard({"ALLOWED_ORIGINS": "*"})


def test_allows_specific_origins_in_production():
    check_cors_wildcard_guard({
        "ALLOWED_ORIGINS": "https://app.example.invalid", "APP_ENV": "production",
    })


def test_raises_when_cors_wildcard_in_production():
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        check_cors_wildcard_guard({"ALLOWED_ORIGINS": "*", "APP_ENV": "production"})


def test_allows_mongo_placeholder_in_development():
    check_mongo_placeholder_guard({"APP_ENV": "development"})


def test_allows_mongo_placeholder_when_app_env_unset():
    check_mongo_placeholder_guard({})


def test_allows_real_mongo_uri_in_production():
    check_mongo_placeholder_guard({
        "MONGODB_URI": "mongodb+srv://real-cluster.example.com/gold_eden",
        "APP_ENV": "production",
    })


def test_allows_placeholder_in_production_when_keyvault_configured():
    # Key Vault is trusted to hold the real secret at request time -- this
    # guard only catches "nothing configured at all", not a KV outage.
    check_mongo_placeholder_guard({
        "AZURE_KEYVAULT_NAME": "fieldcraft-shared-kv",
        "APP_ENV": "production",
    })


def test_raises_when_mongo_uri_unset_in_production():
    with pytest.raises(RuntimeError, match="MONGODB_URI"):
        check_mongo_placeholder_guard({"APP_ENV": "production"})


def test_raises_when_mongo_uri_is_placeholder_in_production():
    with pytest.raises(RuntimeError, match="MONGODB_URI"):
        check_mongo_placeholder_guard({
            "MONGODB_URI": "mongodb://localhost:27017",
            "APP_ENV": "production",
        })


def test_raises_when_mongo_uri_is_blank_in_production():
    with pytest.raises(RuntimeError, match="MONGODB_URI"):
        check_mongo_placeholder_guard({"MONGODB_URI": "   ", "APP_ENV": "production"})
