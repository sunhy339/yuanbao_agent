"""Small ledger helpers for chat-output testing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass(frozen=True)
class Transaction:
    label: str
    amount: Decimal


def parse_amount(value: str) -> Decimal:
    """Parse a user-entered amount into a two-decimal Decimal."""
    try:
        return Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid amount: {value!r}") from exc


def summarize(transactions: Iterable[Transaction]) -> dict[str, Decimal | int]:
    items = list(transactions)
    income = sum((item.amount for item in items if item.amount > 0), Decimal("0.00"))
    expenses = sum((item.amount for item in items if item.amount < 0), Decimal("0.00"))
    return {
        "count": len(items),
        "income": income,
        "expenses": expenses,
        "balance": income + expenses,
    }


def format_balance(amount: Decimal) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def sample_transactions() -> list[Transaction]:
    return [
        Transaction("invoice", parse_amount("1250")),
        Transaction("hosting", parse_amount("-38.50")),
        Transaction("tools", parse_amount("-74.25")),
    ]


def main() -> None:
    summary = summarize(sample_transactions())
    print(f"Balance: {format_balance(summary['balance'])}")


if __name__ == "__main__":
    main()
