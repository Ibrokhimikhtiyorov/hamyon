import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = Path(os.getenv("DB_PATH") or str(DATA_DIR / "market.db"))
FSM_DB_PATH = Path(os.getenv("FSM_DB_PATH") or str(DATA_DIR / "fsm.db"))
FERNET_KEY_PATH = Path(
    os.getenv("FERNET_KEY_PATH") or str(DATA_DIR / "fernet.key")
)
FERNET_KEY = os.getenv("FERNET_KEY", "").strip()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()


def _int_list(raw: str) -> list[int]:
    result: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            result.append(int(chunk))
    return result


ADMIN_IDS = _int_list(os.getenv("ADMIN_IDS", ""))
PAYMENT_CARD = os.getenv("PAYMENT_CARD", "8600 0000 0000 0000").strip()
PAYMENT_HOLDER = os.getenv("PAYMENT_HOLDER", "Marketplace Admin").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@")

COMMISSION_PERCENT = int(os.getenv("COMMISSION_PERCENT", "15"))
CHECK_MINUTES = int(os.getenv("CHECK_MINUTES", "10"))
PENALTY_PERCENT = int(os.getenv("PENALTY_PERCENT", "10"))
PENALTY_INTERVAL_MINUTES = int(os.getenv("PENALTY_INTERVAL_MINUTES", "10"))
AUTO_COMPLETE_MINUTES = int(os.getenv("AUTO_COMPLETE_MINUTES", "60"))
MIN_PRICE = int(os.getenv("MIN_PRICE", "1000"))
MIN_TOPUP = int(os.getenv("MIN_TOPUP", "1000"))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "5000"))

SELLER_SHARE_PERCENT = 100 - COMMISSION_PERCENT

if not 0 <= COMMISSION_PERCENT < 100:
    raise ValueError("COMMISSION_PERCENT 0 va 99 oralig'ida bo'lishi kerak")
if CHECK_MINUTES <= 0 or PENALTY_INTERVAL_MINUTES <= 0 or AUTO_COMPLETE_MINUTES < 0:
    raise ValueError("Vaqt sozlamalari noto'g'ri")
if not 0 <= PENALTY_PERCENT <= 100:
    raise ValueError("PENALTY_PERCENT noto'g'ri")
