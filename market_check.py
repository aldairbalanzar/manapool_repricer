"""Read-only inspection of ManaPool inventory market-price fields."""

from decimal import Decimal, InvalidOperation
import sys

from inventory import fetch_inventory, quantity

MARKET_FIELDS = (
    "market_low",
    "market_price",
    "market_price_foil",
)


def valid_price(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return False
    return amount.is_finite() and amount > 0


def main():
    inventory = fetch_inventory()
    active = [row for row in inventory.values() if quantity(row) > 0]
    print(f"INFO     Active listings: {len(active)}")
    print(f"INFO     Zero-quantity listings excluded: {len(inventory) - len(active)}")
    if not active:
        raise ValueError("No active inventory listings to inspect")

    available_keys = sorted({key for row in active for key in row.keys()})
    price_related_keys = [
        key for key in available_keys
        if any(word in key.lower() for word in ("price", "market", "low"))
    ]
    print("INFO     Pricing-related API field names: " + (
        ", ".join(price_related_keys) if price_related_keys else "none"
    ))

    for field in MARKET_FIELDS:
        present = sum(field in row for row in active)
        usable = sum(valid_price(row.get(field)) for row in active)
        print(f"INFO     {field}: present={present}, usable={usable}, active={len(active)}")

    if any(valid_price(row.get("market_low")) for row in active):
        print("SUCCESS  market_low is available on at least one active API listing")
        print("INFO     Next: verify market_low comparability and missing values before repricing")
    else:
        print("WARNING  No usable market_low values found in the inventory API response")
        print("INFO     Another documented market-price source will be needed")

    print("SUCCESS  Read-only market-price inspection completed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Avoid leaking response bodies or request URLs that may contain secrets.
        print(f"ERROR    Market-price inspection failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
