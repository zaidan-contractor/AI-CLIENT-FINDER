from decimal import Decimal
from typing import Dict, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.tenancy import ISOCurrencyRegistry

# Standard Global ISO-4217 Initial Seed Data
GLOBAL_CURRENCIES: Dict[str, Dict[str, Any]] = {
    "USD": {"name": "United States Dollar", "minor_units": 2},
    "EUR": {"name": "Euro", "minor_units": 2},
    "GBP": {"name": "British Pound Sterling", "minor_units": 2},
    "INR": {"name": "Indian Rupee", "minor_units": 2},
    "CAD": {"name": "Canadian Dollar", "minor_units": 2},
    "AUD": {"name": "Australian Dollar", "minor_units": 2},
    "JPY": {"name": "Japanese Yen", "minor_units": 0},
    "AED": {"name": "United Arab Emirates Dirham", "minor_units": 2},
    "SGD": {"name": "Singapore Dollar", "minor_units": 2},
    "CHF": {"name": "Swiss Franc", "minor_units": 2},
    "CNY": {"name": "Chinese Yuan", "minor_units": 2},
    "BRL": {"name": "Brazilian Real", "minor_units": 2},
    "ZAR": {"name": "South African Rand", "minor_units": 2},
    "NZD": {"name": "New Zealand Dollar", "minor_units": 2},
    "MXN": {"name": "Mexican Peso", "minor_units": 2},
    "HKD": {"name": "Hong Kong Dollar", "minor_units": 2},
    "SEK": {"name": "Swedish Krona", "minor_units": 2},
    "NOK": {"name": "Norwegian Krone", "minor_units": 2},
    "KRW": {"name": "South Korean Won", "minor_units": 0},
    "SAR": {"name": "Saudi Riyal", "minor_units": 2},
}

def seed_currency_registry(db: Session):
    for code, info in GLOBAL_CURRENCIES.items():
        existing = db.execute(
            select(ISOCurrencyRegistry).where(ISOCurrencyRegistry.currency_code == code)
        ).scalars().first()
        if not existing:
            rec = ISOCurrencyRegistry(
                currency_code=code,
                currency_name=info["name"],
                minor_units=info["minor_units"],
                is_active=True
            )
            db.add(rec)
    db.commit()

def is_valid_currency_code(code: str, db: Optional[Session] = None) -> bool:
    clean_code = code.strip().upper()
    if clean_code in GLOBAL_CURRENCIES:
        return True
    if db:
        rec = db.execute(
            select(ISOCurrencyRegistry).where(
                ISOCurrencyRegistry.currency_code == clean_code,
                ISOCurrencyRegistry.is_active == True
            )
        ).scalars().first()
        return rec is not None
    return False

def get_currency_minor_units(code: str, db: Optional[Session] = None) -> int:
    clean_code = code.strip().upper()
    if clean_code in GLOBAL_CURRENCIES:
        return GLOBAL_CURRENCIES[clean_code]["minor_units"]
    if db:
        rec = db.execute(
            select(ISOCurrencyRegistry).where(ISOCurrencyRegistry.currency_code == clean_code)
        ).scalars().first()
        if rec:
            return rec.minor_units
    return 2  # Default standard minor unit
