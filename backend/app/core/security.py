import hmac
import hashlib
import base64
import os
import secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

def verify_password(plain_password: str, hashed_password: str) -> bool:
    pwd_bytes = plain_password.encode("utf-8")[:72]
    hashed_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(pwd_bytes, hashed_bytes)

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

def create_access_token(
    user_id: str,
    organization_id: str,
    active_workspace_id: str,
    role: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "sub": user_id,
        "user_id": user_id,
        "organization_id": organization_id,
        "active_workspace_id": active_workspace_id,
        "role": role,
        "exp": expire,
        "iat": now
    }
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

# --- Envelope Encryption (AES-256-GCM) ---

def _get_kek() -> bytes:
    try:
        raw_key = base64.b64decode(settings.ENCRYPTION_MASTER_KEY)
        if len(raw_key) != 32:
            raise ValueError("ENCRYPTION_MASTER_KEY must be exactly 32 bytes (256 bits).")
        return raw_key
    except Exception as e:
        raise RuntimeError(f"Cryptographic Initialization Error: Invalid KEK: {e}")

def encrypt_credential(plaintext: str) -> str:
    """
    Envelope Encryption:
    Generates a unique 256-bit Data Encryption Key (DEK),
    encrypts plaintext with DEK via AES-256-GCM,
    encrypts DEK with master Key Encryption Key (KEK) via AES-256-GCM,
    returns serialized payload: dek_ciphertext:dek_nonce:data_ciphertext:data_nonce
    """
    kek = _get_kek()
    dek = AESGCM.generate_key(bit_length=256)
    
    # Encrypt DEK with KEK
    kek_cipher = AESGCM(kek)
    dek_nonce = os.urandom(12)
    encrypted_dek = kek_cipher.encrypt(dek_nonce, dek, None)
    
    # Encrypt Data with DEK
    dek_cipher = AESGCM(dek)
    data_nonce = os.urandom(12)
    encrypted_data = dek_cipher.encrypt(data_nonce, plaintext.encode("utf-8"), None)
    
    payload = {
        "ed": base64.b64encode(encrypted_dek).decode("ascii"),
        "en": base64.b64encode(dek_nonce).decode("ascii"),
        "cd": base64.b64encode(encrypted_data).decode("ascii"),
        "cn": base64.b64encode(data_nonce).decode("ascii")
    }
    return f"v1:{payload['ed']}:{payload['en']}:{payload['cd']}:{payload['cn']}"

def decrypt_credential(ciphertext_payload: str) -> str:
    parts = ciphertext_payload.split(":")
    if len(parts) != 5 or parts[0] != "v1":
        raise ValueError("Invalid encrypted credential format.")
    
    kek = _get_kek()
    encrypted_dek = base64.b64decode(parts[1])
    dek_nonce = base64.b64decode(parts[2])
    encrypted_data = base64.b64decode(parts[3])
    data_nonce = base64.b64decode(parts[4])
    
    # Decrypt DEK with KEK
    kek_cipher = AESGCM(kek)
    dek = kek_cipher.decrypt(dek_nonce, encrypted_dek, None)
    
    # Decrypt Data with DEK
    dek_cipher = AESGCM(dek)
    plaintext = dek_cipher.decrypt(data_nonce, encrypted_data, None)
    return plaintext.decode("utf-8")

# --- Key-Versioned Cryptographic Suppression Hashing ---

def canonicalize_email(email: str) -> str:
    """
    Deterministic Email Canonicalization:
    - Transport whitespace trimmed
    - Domain lowercased & punycode-normalized
    - Local part lowercased
    - Avoids aggressive dot/plus removal (preserves RFC 5322 identity)
    """
    clean = email.strip()
    if "@" not in clean:
        return clean.lower()
    local_part, domain = clean.rsplit("@", 1)
    local_part = local_part.strip().lower()
    try:
        domain = domain.strip().lower().encode("idna").decode("ascii")
    except Exception:
        domain = domain.strip().lower()
    return f"{local_part}@{domain}"

def _get_suppression_key(key_version: int) -> bytes:
    if key_version == 1:
        return settings.SUPPRESSION_HMAC_KEY_V1.encode("utf-8")
    elif key_version == 2:
        if not settings.SUPPRESSION_HMAC_KEY_V2:
            raise ValueError("SUPPRESSION_HMAC_KEY_V2 is not configured.")
        return settings.SUPPRESSION_HMAC_KEY_V2.encode("utf-8")
    else:
        raise ValueError(f"Unsupported suppression key version: {key_version}")

def hash_suppression_target(email: str, key_version: Optional[int] = None) -> Tuple[str, int, str]:
    """
    Generates HMAC-SHA256 suppression hash.
    Returns (suppression_hash, key_version, algorithm).
    """
    version = key_version if key_version is not None else settings.SUPPRESSION_ACTIVE_KEY_VERSION
    key = _get_suppression_key(version)
    canonical = canonicalize_email(email)
    h = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256)
    return h.hexdigest(), version, "HMAC-SHA256"

def verify_suppression_match(email: str, recorded_hash: str, recorded_version: int) -> bool:
    try:
        key = _get_suppression_key(recorded_version)
        canonical = canonicalize_email(email)
        expected = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, recorded_hash)
    except Exception:
        return False

# --- Opaque High-Entropy Unsubscribe Tokens ---

def generate_unsubscribe_token() -> str:
    """Generates 32-byte cryptographically secure high-entropy URL-safe token."""
    return secrets.token_urlsafe(32)

# --- Anti-CSRF Token Generation & Verification for Browser Confirmation ---

def generate_csrf_token(unsub_token: str) -> str:
    """
    Generates anti-CSRF token cryptographically bound to unsub_token and timestamp.
    """
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    message = f"{unsub_token}:{timestamp}".encode("utf-8")
    signature = hmac.new(settings.CSRF_SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{timestamp}:{signature}"

def verify_csrf_token(unsub_token: str, csrf_token: str, max_age_seconds: int = 3600) -> bool:
    try:
        parts = csrf_token.split(":")
        if len(parts) != 2:
            return False
        timestamp_str, signature = parts
        timestamp = int(timestamp_str)
        now = int(datetime.now(timezone.utc).timestamp())
        if now - timestamp > max_age_seconds or now < timestamp - 60:
            return False  # Expired or from the future
        
        message = f"{unsub_token}:{timestamp_str}".encode("utf-8")
        expected_sig = hmac.new(settings.CSRF_SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_sig, signature)
    except Exception:
        return False
