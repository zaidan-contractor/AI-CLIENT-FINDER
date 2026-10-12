import pytest
from app.core.security import (
    canonicalize_email,
    encrypt_credential,
    decrypt_credential,
    hash_suppression_target,
    verify_suppression_match,
    create_access_token,
    decode_access_token
)

def test_email_canonicalization():
    # Whitespace and case
    assert canonicalize_email("  User.Name@Example.COM  ") == "user.name@example.com"
    # Preserves local part dots and plus signs (does not aggressively strip)
    assert canonicalize_email("john.doe+news@gmail.com") == "john.doe+news@gmail.com"
    # Punycode domain
    assert canonicalize_email("contact@münchen.de") == "contact@xn--mnchen-3ya.de"

def test_envelope_encryption():
    secret = "sk_live_very_secret_provider_credential_12345"
    encrypted = encrypt_credential(secret)
    assert encrypted.startswith("v1:")
    assert secret not in encrypted

    decrypted = decrypt_credential(encrypted)
    assert decrypted == secret

def test_key_versioned_suppression_hashing():
    email = "test.suppression@example.com"
    hash_v1, ver_1, algo_1 = hash_suppression_target(email, key_version=1)
    assert ver_1 == 1
    assert algo_1 == "HMAC-SHA256"
    assert verify_suppression_match(email, hash_v1, 1) is True
    assert verify_suppression_match("different@example.com", hash_v1, 1) is False

def test_jwt_access_token():
    token = create_access_token(
        user_id="user-123",
        organization_id="org-abc",
        active_workspace_id="ws-xyz",
        role="Operator"
    )
    payload = decode_access_token(token)
    assert payload["user_id"] == "user-123"
    assert payload["organization_id"] == "org-abc"
    assert payload["active_workspace_id"] == "ws-xyz"
    assert payload["role"] == "Operator"
