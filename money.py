import re
from decimal import Decimal

MAX_AMOUNT_MINOR = 100_000_000_000
AMOUNT_PATTERN = re.compile(r"[0-9]{1,10}(?:\.[0-9]{1,2})?")


def parse_minor(value: str) -> int:
    if not AMOUNT_PATTERN.fullmatch(value):
        raise ValueError("Enter an amount with at most two decimal places")
    amount = int(Decimal(value) * 100)
    if not 0 < amount <= MAX_AMOUNT_MINOR:
        raise ValueError("Amount must be between KSh 0.01 and KSh 1,000,000,000.00")
    return amount


def legacy_minor(value) -> int:
    amount = Decimal(str(value))
    if not amount.is_finite() or not 0 <= amount <= Decimal(MAX_AMOUNT_MINOR) / 100:
        raise ValueError("Legacy amount is out of range")
    minor = amount * 100
    if minor != minor.to_integral_value():
        raise ValueError("Legacy amount requires reconciliation to two decimal places")
    return int(minor)


def as_money(minor: int) -> Decimal:
    return Decimal(minor).scaleb(-2)
