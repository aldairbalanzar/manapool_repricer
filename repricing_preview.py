"""Read-only ManaPool repricing preview. NEVER writes to ManaPool."""
import argparse
import csv
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from inventory import fetch_inventory, quantity

DEFAULT_CATALOG_URL = "https://manapool.com/api/v1/products/singles"
FIELDS = [
    "product_id", "card_name", "set_code", "collector_number", "condition", "finish", "language",
    "quantity", "current_price", "market_low", "target_price", "price_difference",
    "below_market_pct", "is_below_market", "approved", "verification_status",
    "market_available_quantity", "price_as_of", "notes",
]


def cents(value, *, positive=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d != d.to_integral_value() or d < (1 if positive else 0):
            return None
        return int(d)
    except (InvalidOperation, TypeError, ValueError):
        return None


def money(value):
    return "" if value is None else f"{Decimal(value) / 100:.2f}"


def fetch_catalog():
    print("INFO     Fetching public product singles catalog (GET only)")

    url = "https://manapool.com/api/v1/products/singles"

    response = requests.get(url, timeout=120)

    print(f"INFO     Catalog HTTP status: {response.status_code}")

    if not response.ok:
        try:
            error = response.json()
            print(f"ERROR    Catalog API error: {str(error)[:1000]}")
        except ValueError:
            print("ERROR    Catalog returned a non-JSON error response")

        response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError("Unexpected catalog response format")

    data = payload.get("data")

    if not isinstance(data, list):
        raise ValueError("Catalog response missing data array")

    print(f"SUCCESS  Catalog returned {len(data)} records")

    return data


def index_variants(cards):
    by_product = {}
    duplicate_ids = set()
    variants_count = 0
    for card in cards:
        if not isinstance(card, dict):
            raise ValueError("Non-object catalog card")
        variants = card.get("variants", [])
        if not isinstance(variants, list):
            raise ValueError("Catalog variants must be an array")
        for variant in variants:
            if not isinstance(variant, dict):
                raise ValueError("Non-object catalog variant")
            pid = str(variant.get("product_id") or "").strip()
            if not pid:
                continue
            variants_count += 1
            if pid in by_product:
                duplicate_ids.add(pid)
            else:
                by_product[pid] = (card, variant)
    if duplicate_ids:
        raise ValueError(f"Catalog has {len(duplicate_ids)} duplicate variant product IDs")
    print(f"INFO     Indexed variants: {variants_count}")
    return by_product


def report(inventory, variants, as_of, path):
    active = [(pid, row) for pid, row in inventory.items() if quantity(row) > 0]
    print(f"INFO     Active listings: {len(active)}")
    print(f"INFO     Zero-quantity listings excluded: {len(inventory) - len(active)}")
    stats = Counter()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for pid, row in sorted(active):
            current = cents(row.get("price_cents"))
            entry = {
                "product_id": pid, "quantity": quantity(row), "current_price": money(current),
                "approved": "false", "is_below_market": "false", "price_as_of": as_of or "",
            }
            match = variants.get(pid)
            if not match:
                entry.update(verification_status="unmatched_product_id", notes="No matching catalog variant")
                stats["unmatched_product_id"] += 1
            else:
                card, variant = match
                entry.update(
                    card_name=card.get("name") or "", set_code=card.get("set_code") or "",
                    collector_number=card.get("number") or "", condition=variant.get("condition_id") or "",
                    finish=variant.get("finish_id") or "", language=variant.get("language_id") or "",
                    market_available_quantity=variant.get("available_quantity", ""),
                )
                low = cents(variant.get("low_price"), positive=True)
                available = variant.get("available_quantity")
                entry["market_low"] = money(low)
                if current is None:
                    entry.update(verification_status="invalid_current_price", notes="Missing or invalid inventory price_cents")
                    stats["invalid_current_price"] += 1
                elif low is None:
                    entry.update(verification_status="no_market_low", notes="Missing or invalid variant low_price")
                    stats["no_market_low"] += 1
                elif cents(available, positive=True) is None:
                    entry.update(verification_status="no_market_supply", notes="No valid positive available_quantity")
                    stats["no_market_supply"] += 1
                else:
                    target = max(75, low - 1)
                    entry["target_price"] = money(target)
                    entry["price_difference"] = money(target - current)
                    if low > 500 and current * 100 <= low * 80:
                        entry["is_below_market"] = "true"
                        entry["below_market_pct"] = f"{(Decimal(low-current) * 100 / Decimal(low)):.2f}"
                    elif current < low:
                        entry["below_market_pct"] = f"{(Decimal(low-current) * 100 / Decimal(low)):.2f}"
                    else:
                        entry["below_market_pct"] = "0.00"
                    # The feed may include this seller's own listings. Never auto-approve.
                    if low == current:
                        status = "review_self_low_possible"
                        notes = "Market low equals your listing; avoid repeated self-undercutting"
                    else:
                        status = "review_required"
                        notes = "Preview only; market low may include your own/other listings"
                    entry.update(verification_status=status, notes=notes)
                    stats[status] += 1
            writer.writerow(entry)
    print(f"SUCCESS  Report written: {path} ({len(active)} rows)")
    for status, count in sorted(stats.items()):
        print(f"INFO     {status}: {count}")
    print("WARNING  Prices are proposals only; catalog may include your own listings")
    print("SUCCESS  Read-only repricing preview completed; NO INVENTORY CHANGES")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("repricing_report.csv"))
    parser.add_argument("--catalog-url", default=os.getenv("MANAPOOL_SINGLES_URL", DEFAULT_CATALOG_URL))
    args = parser.parse_args()
    inventory = fetch_inventory()
    catalog, as_of = fetch_catalog(args.catalog_url)
    variants = index_variants(catalog)
    report(inventory, variants, as_of, args.output)


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, ValueError, KeyError, RuntimeError, OSError) as exc:
        print(f"ERROR    Preview failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
