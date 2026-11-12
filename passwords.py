import hashlib
import hmac
import secrets

LEGACY_ITERATIONS = 310_000
ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 256


def valid_new_password(password: str) -> bool:
    return MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH and bool(password.strip())


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        algorithm, *fields = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        if len(fields) == 2:
            salt, digest = fields
            iterations = LEGACY_ITERATIONS
        else:
            work_factor, salt, digest = fields
            iterations = int(work_factor)
        if iterations not in {LEGACY_ITERATIONS, ITERATIONS}:
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except ValueError:
        return False
