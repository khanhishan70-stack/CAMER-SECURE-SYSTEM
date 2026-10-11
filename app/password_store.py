import hashlib
import json
import os

from app.config import DATA_DIR

PASSWORD_FILE = DATA_DIR / "password.hash"


def set_password(password: str) -> None:
    salt = os.urandom(32)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    data = {"salt": salt.hex(), "hash": h.hex()}
    PASSWORD_FILE.write_text(json.dumps(data), encoding="utf-8")


def check_password(password: str) -> bool:
    if not PASSWORD_FILE.is_file():
        return False
    data = json.loads(PASSWORD_FILE.read_text(encoding="utf-8"))
    salt = bytes.fromhex(data["salt"])
    stored = bytes.fromhex(data["hash"])
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return h == stored


def has_password() -> bool:
    return PASSWORD_FILE.is_file()


def clear_password() -> None:
    if PASSWORD_FILE.is_file():
        PASSWORD_FILE.unlink()
