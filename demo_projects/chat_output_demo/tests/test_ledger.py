from decimal import Decimal
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ledger import Transaction, format_balance, parse_amount, summarize


def test_parse_amount_quantizes_to_cents():
    assert parse_amount("12.345") == Decimal("12.34")


def test_summarize_splits_income_and_expenses():
    result = summarize(
        [
            Transaction("invoice", Decimal("100.00")),
            Transaction("hosting", Decimal("-25.50")),
            Transaction("tools", Decimal("-10.00")),
        ]
    )

    assert result["count"] == 3
    assert result["income"] == Decimal("100.00")
    assert result["expenses"] == Decimal("-35.50")
    assert result["balance"] == Decimal("64.50")


def test_format_balance_uses_currency_style():
    assert format_balance(Decimal("64.5")) == "$64.50"
    assert format_balance(Decimal("-10")) == "-$10.00"
