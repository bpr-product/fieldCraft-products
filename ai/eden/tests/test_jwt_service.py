import time

import pytest
from jose import jwk, jwt as jose_jwt

from source.utils import jwt_service

TEST_OPENID_URL = "https://test-tenant.b2clogin.com/test-tenant.onmicrosoft.com/v2.0/.well-known/openid-configuration"
TEST_ISSUER = "https://test-tenant.b2clogin.com/00000000-0000-0000-0000-000000000000/v2.0/"
TEST_JWKS_URI = "https://test-tenant.b2clogin.com/test-tenant.onmicrosoft.com/discovery/v2.0/keys"
TEST_AUDIENCE = "test-client-id"


@pytest.fixture
def rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_jwk = jwk.construct(private_pem, algorithm="RS256").to_dict()
    public_jwk["kid"] = "test-kid"
    public_jwk["use"] = "sig"
    return private_pem, public_jwk


@pytest.fixture
def configure_single_tenant(monkeypatch, rsa_keypair):
    """Configures jwt_service with exactly one trusted B2C tenant (TEST_ISSUER
    / TEST_JWKS_URI / TEST_AUDIENCE), with the discovery + JWKS fetches
    stubbed out -- no real network calls. Returns (private_pem, public_jwk)
    so tests can sign tokens against it."""
    private_pem, public_jwk = rsa_keypair
    monkeypatch.setattr(jwt_service, "AUTH_OPENID_CONFIG_URLS", [TEST_OPENID_URL])
    monkeypatch.setattr(jwt_service, "AUTH_AUDIENCES", [TEST_AUDIENCE])
    monkeypatch.setattr(
        jwt_service,
        "_openid_config",
        lambda url: {"issuer": TEST_ISSUER, "jwks_uri": TEST_JWKS_URI},
    )
    monkeypatch.setattr(
        jwt_service, "_fetch_jwks", lambda jwks_uri, force=False: [public_jwk]
    )
    return private_pem, public_jwk


def _make_token(private_pem, claims, kid="test-kid"):
    return jose_jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def _base_claims(**overrides):
    now = int(time.time())
    claims = {
        "oid": "test-user-oid-1234",
        "tenantId": "test-tenant-5678",
        "aud": TEST_AUDIENCE,
        "iss": TEST_ISSUER,
        "exp": now + 300,
        "nbf": now - 10,
    }
    claims.update(overrides)
    return claims


def test_validate_token_accepts_correctly_signed_token(configure_single_tenant):
    private_pem, _ = configure_single_tenant
    token = _make_token(private_pem, _base_claims())
    claims = jwt_service.validate_token(token)

    assert claims["oid"] == "test-user-oid-1234"
    assert claims["tenantId"] == "test-tenant-5678"


def test_validate_token_rejects_wrong_audience(configure_single_tenant):
    private_pem, _ = configure_single_tenant
    token = _make_token(private_pem, _base_claims(aud="some-other-app-id"))
    with pytest.raises(jwt_service.TokenValidationError):
        jwt_service.validate_token(token)


def test_validate_token_rejects_wrong_issuer(configure_single_tenant):
    private_pem, _ = configure_single_tenant
    token = _make_token(private_pem, _base_claims(iss="https://attacker.example.com/"))
    with pytest.raises(jwt_service.TokenValidationError):
        jwt_service.validate_token(token)


def test_validate_token_rejects_expired_token(configure_single_tenant):
    private_pem, _ = configure_single_tenant
    token = _make_token(private_pem, _base_claims(exp=int(time.time()) - 60))
    with pytest.raises(jwt_service.TokenValidationError):
        jwt_service.validate_token(token)


def test_validate_token_rejects_missing_oid(configure_single_tenant):
    private_pem, _ = configure_single_tenant
    claims = _base_claims()
    del claims["oid"]
    token = _make_token(private_pem, claims)
    with pytest.raises(jwt_service.TokenValidationError):
        jwt_service.validate_token(token)


def test_validate_token_rejects_unknown_kid(configure_single_tenant):
    private_pem, _ = configure_single_tenant
    token = _make_token(private_pem, _base_claims(), kid="some-other-kid")
    with pytest.raises(jwt_service.TokenValidationError):
        jwt_service.validate_token(token)


def test_validate_token_accepts_second_configured_tenant(monkeypatch, rsa_keypair):
    """Multi-tenant: a token from a SECOND configured tenant (different
    issuer/JWKS/audience) validates too -- this is the actual behavior the
    single-tenant scheme couldn't do at all."""
    private_pem, public_jwk = rsa_keypair
    other_openid_url = "https://other-tenant.b2clogin.com/other-tenant.onmicrosoft.com/v2.0/.well-known/openid-configuration"
    other_issuer = "https://other-tenant.b2clogin.com/11111111-1111-1111-1111-111111111111/v2.0/"
    other_jwks_uri = "https://other-tenant.b2clogin.com/other-tenant.onmicrosoft.com/discovery/v2.0/keys"
    other_audience = "other-client-id"

    monkeypatch.setattr(
        jwt_service, "AUTH_OPENID_CONFIG_URLS", [TEST_OPENID_URL, other_openid_url]
    )
    monkeypatch.setattr(jwt_service, "AUTH_AUDIENCES", [TEST_AUDIENCE, other_audience])

    configs = {
        TEST_OPENID_URL: {"issuer": TEST_ISSUER, "jwks_uri": TEST_JWKS_URI},
        other_openid_url: {"issuer": other_issuer, "jwks_uri": other_jwks_uri},
    }
    monkeypatch.setattr(jwt_service, "_openid_config", lambda url: configs[url])
    monkeypatch.setattr(
        jwt_service, "_fetch_jwks", lambda jwks_uri, force=False: [public_jwk]
    )

    token = _make_token(
        private_pem, _base_claims(iss=other_issuer, aud=other_audience)
    )
    claims = jwt_service.validate_token(token)
    assert claims["oid"] == "test-user-oid-1234"


def test_get_authenticated_subject_wires_auth_context(monkeypatch, configure_single_tenant):
    from fastapi import HTTPException
    from source.utils import auth as auth_module
    from source.utils.auth import get_authenticated_subject

    private_pem, _ = configure_single_tenant
    # Tenant comes from a DB lookup on the verified oid, never a JWT claim
    # (real Azure AD B2C tokens carry no tenant claim at all).
    monkeypatch.setattr(
        auth_module._role_resolver, "resolve_tenant_id", lambda user_uid: "test-tenant-5678"
    )
    monkeypatch.setattr(
        auth_module._role_resolver, "get_roles", lambda tenant_id, user_id: ["admin"]
    )
    monkeypatch.setattr(
        auth_module._role_resolver, "get_permissions", lambda tenant_id, user_id: ["PRO.REA"]
    )

    token = _make_token(private_pem, _base_claims())
    auth = get_authenticated_subject(authorization=f"Bearer {token}")
    assert auth.user_id == "test-user-oid-1234"
    assert auth.tenant_id == "test-tenant-5678"
    assert auth.roles == ["admin"]
    assert auth.permissions == ["PRO.REA"]

    with pytest.raises(HTTPException) as exc_info:
        get_authenticated_subject(authorization=None)
    assert exc_info.value.status_code == 401


def test_get_authenticated_subject_rejects_ambiguous_tenant(monkeypatch, configure_single_tenant):
    from fastapi import HTTPException
    from source.utils import auth as auth_module
    from source.utils.auth import get_authenticated_subject

    private_pem, _ = configure_single_tenant
    monkeypatch.setattr(auth_module._role_resolver, "resolve_tenant_id", lambda user_uid: None)

    token = _make_token(private_pem, _base_claims())
    with pytest.raises(HTTPException) as exc_info:
        get_authenticated_subject(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 403
