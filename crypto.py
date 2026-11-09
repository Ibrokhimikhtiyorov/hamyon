import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from config import FERNET_KEY, FERNET_KEY_PATH

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _load_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    if FERNET_KEY:
        key = FERNET_KEY.encode("ascii")
    elif FERNET_KEY_PATH.exists():
        key = FERNET_KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        FERNET_KEY_PATH.write_bytes(key)
        try:
            FERNET_KEY_PATH.chmod(0o600)
        except OSError:
            pass

    _fernet = Fernet(key)
    return _fernet


def encrypt(value: str) -> str:
    return _load_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _load_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        logger.error("Shifrlangan ma'lumotni ochishda xatolik: %s", exc)
        raise ValueError("Ma'lumotni shifrdan ochib bo'lmadi") from exc


def login_hash(login: str) -> str:
    normalized = login.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
