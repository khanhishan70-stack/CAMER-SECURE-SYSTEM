import base64
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from cryptography.fernet import Fernet

from app.config import DATA_DIR, EMBEDDING_FILE, SALT_FILE


def _derive_key() -> bytes:
    SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SALT_FILE.exists():
        SALT_FILE.write_bytes(os.urandom(16))
    salt = SALT_FILE.read_bytes()
    machine = str(uuid.getnode()).encode("utf-8")
    raw = hashlib.pbkdf2_hmac("sha256", machine, salt, 480_000, dklen=32)
    return base64.urlsafe_b64encode(raw)


def _fernet() -> Fernet:
    return Fernet(_derive_key())


def has_enrollment() -> bool:
    return EMBEDDING_FILE.is_file()


def save_embedding(embedding: list[float]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"embedding": embedding, "v": 1}).encode("utf-8")
    token = _fernet().encrypt(payload)
    EMBEDDING_FILE.write_bytes(token)


def load_embedding() -> np.ndarray | None:
    if not EMBEDDING_FILE.is_file():
        return None
    try:
        plain = _fernet().decrypt(EMBEDDING_FILE.read_bytes())
        data: dict[str, Any] = json.loads(plain.decode("utf-8"))
        emb = data.get("embedding")
        if not emb:
            return None
        return np.asarray(emb, dtype=np.float64)
    except Exception:
        return None


def clear_enrollment() -> None:
    if EMBEDDING_FILE.exists():
        EMBEDDING_FILE.unlink()
