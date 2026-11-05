import html
import re
import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

SELLER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

GAME_ACCOUNT_EXAMPLES = (
    "Standoff 2, Brawl Stars, FIFA, PUBG Mobile va shunga o'xshash o'yin akkauntlari"
)

ACCOUNT_STATUS_LABELS = {
    "active": "🟢 Sotuvda",
    "paused": "⏸ To'xtatilgan",
    "sold": "🔴 Sotilgan",
    "refunded": "↩️ Qaytarilgan",
}

ORDER_STATUS_LABELS = {
    "checking": "Tekshirilmoqda",
    "completed": "Yakunlangan",
    "auto_completed": "Avtomatik yakunlangan",
    "disputed": "Shikoyat ko'rib chiqilmoqda",
    "refunded": "Pul qaytarilgan",
}

TOPUP_STATUS_LABELS = {
    "pending": "Kutilmoqda",
    "approved": "Tasdiqlangan",
    "rejected": "Rad etilgan",
}

WITHDRAW_STATUS_LABELS = {
    "pending": "Kutilmoqda",
    "approved": "To'langan",
    "rejected": "Rad etilgan",
}


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def fmt_sum(amount: int) -> str:
    return f"{int(amount):,}".replace(",", " ") + " so'm"


def fmt_dt(value: str | datetime | None) -> str:
    if value is None:
        return "-"
    dt = parse_dt(value) if isinstance(value, str) else value
    if dt is None:
        return "-"
    local = dt.replace(tzinfo=UTC).astimezone(TASHKENT_TZ)
    return local.strftime("%d.%m.%Y %H:%M")


def fmt_time(value: str | datetime) -> str:
    dt = parse_dt(value) if isinstance(value, str) else value
    local = dt.replace(tzinfo=UTC).astimezone(TASHKENT_TZ)
    return local.strftime("%H:%M:%S")


def remaining_text(deadline: str | datetime) -> str:
    dt = parse_dt(deadline) if isinstance(deadline, str) else deadline
    seconds = int((dt - utcnow()).total_seconds())
    if seconds <= 0:
        overdue = -seconds
        minutes = overdue // 60
        return f"muddat {minutes} daqiqa oldin tugagan"
    minutes, secs = divmod(seconds, 60)
    return f"{minutes} daqiqa {secs} soniya qoldi"


def esc(value: object) -> str:
    return html.escape(str(value) if value is not None else "")


def code(value: object) -> str:
    return f"<code>{esc(value)}</code>"


def generate_code(length: int = 8) -> str:
    return "".join(secrets.choice(SELLER_CODE_ALPHABET) for _ in range(length))


def parse_amount(text: str) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    if not digits or len(digits) > 12:
        return None
    return int(digits)


def normalize_seller_code(text: str) -> str:
    return re.sub(r"\s+", "", text or "").upper()


def clean_card(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def mask_card(card: str) -> str:
    if len(card) < 8:
        return card
    return f"{card[:4]} **** **** {card[-4:]}"


def mask_name(full_name: str | None, tg_id: int) -> str:
    name = (full_name or "").strip()
    if name:
        return f"{esc(name)} (ID: {tg_id})"
    return f"ID: {tg_id}"


def deadline_after(minutes: int) -> str:
    return (utcnow() + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
