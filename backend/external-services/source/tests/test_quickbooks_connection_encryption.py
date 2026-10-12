import json

import pytest

from source.Utils import constants as const
from source.dal.QuickBooksRepository import QuickBooksRepository


def test_quickbooks_connection_secrets_are_encrypted_and_decrypted(monkeypatch):
    monkeypatch.setenv("SALT", "$@@FIELDCRAFT@@$")
    monkeypatch.setenv("IV", "#GR@CeFU!MGWT4&$")
    monkeypatch.setenv("QUICKBOOKS_ENCRYPTION_PASSWORD", "quickbooks-test-context")

    repo = QuickBooksRepository()
    payload = {
        const.KEY_REALM_ID: "REALM12345",
        const.KEY_REFRESH_TOKEN: "refresh-token-value",
        const.KEY_ACCESS_TOKEN: "access-token-value",
        const.KEY_ENV: "sandbox",
    }

    encrypted = repo._encrypt_connection_secrets(payload)

    assert encrypted[const.KEY_REALM_ID].startswith(repo._ENCRYPTED_VALUE_PREFIX)
    assert encrypted[const.KEY_REFRESH_TOKEN].startswith(repo._ENCRYPTED_VALUE_PREFIX)
    assert encrypted[const.KEY_ACCESS_TOKEN].startswith(repo._ENCRYPTED_VALUE_PREFIX)
    assert encrypted[const.KEY_REALM_ID] != payload[const.KEY_REALM_ID]
    assert encrypted[const.KEY_REFRESH_TOKEN] != payload[const.KEY_REFRESH_TOKEN]
    assert encrypted[const.KEY_ACCESS_TOKEN] != payload[const.KEY_ACCESS_TOKEN]
    assert encrypted[repo._REALM_ID_HASH_KEY]
    assert payload[const.KEY_REALM_ID] not in encrypted[repo._REALM_ID_HASH_KEY]

    decrypted = repo._decrypt_connection_secrets(encrypted)

    assert decrypted[const.KEY_REALM_ID] == payload[const.KEY_REALM_ID]
    assert decrypted[const.KEY_REFRESH_TOKEN] == payload[const.KEY_REFRESH_TOKEN]
    assert decrypted[const.KEY_ACCESS_TOKEN] == payload[const.KEY_ACCESS_TOKEN]
    assert decrypted[const.KEY_ENV] == payload[const.KEY_ENV]


def test_parse_additional_info_decrypts_encrypted_connection_values(monkeypatch):
    monkeypatch.setenv("SALT", "$@@FIELDCRAFT@@$")
    monkeypatch.setenv("IV", "#GR@CeFU!MGWT4&$")
    monkeypatch.setenv("QUICKBOOKS_ENCRYPTION_PASSWORD", "quickbooks-test-context")

    repo = QuickBooksRepository()
    encrypted = repo._encrypt_connection_secrets(
        {
            const.KEY_REALM_ID: "REALM12345",
            const.KEY_REFRESH_TOKEN: "refresh-token-value",
            const.KEY_ENV: "sandbox",
        }
    )

    parsed = repo.parseAdditionalInfo(json.dumps(encrypted))

    assert parsed[const.KEY_REALM_ID] == "REALM12345"
    assert parsed[const.KEY_REFRESH_TOKEN] == "refresh-token-value"
    assert parsed[const.KEY_ENV] == "sandbox"


def test_get_status_returns_decrypted_realm_id(monkeypatch):
    repo = QuickBooksRepository()

    monkeypatch.setattr(
        repo,
        "getAdditionalInfo",
        lambda subscriber_id, service_id: {
            const.KEY_STATUS: const.STATUS_CONNECTED,
            const.KEY_REALM_ID: "REALM12345",
            const.KEY_REFRESH_TOKEN: "refresh-token-value",
            const.KEY_ENV: "sandbox",
        },
    )

    status = repo.getStatus("tenant-1", "service-1")

    assert status["connected"] is True
    assert status["realmId"] == "REALM12345"
    assert status["realmIdPresent"] is True


def test_realm_id_hash_requires_salt(monkeypatch):
    monkeypatch.delenv("SALT", raising=False)

    repo = QuickBooksRepository()

    with pytest.raises(RuntimeError, match="SALT must be configured before hashing QuickBooks realm IDs"):
        repo._realm_id_hash("REALM12345")
