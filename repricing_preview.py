
import csv
import os
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from inventory import fetch_inventory, quantity

CATALOG_URL = "https://manapool.com/api/v1/products/singles"
REPORT_FILE = Path("repricing_report.csv")

MIN_PRICE_CENTS = 75
UNDERCUT_CENTS = 1
BELOW_MARKET_THRESHOLD = Decimal("0.20")
BELOW_MARKET_MIN_CENTS = 500


def log(level, message):
    print(f"{level:<8} {message}", flush=True)


def cents(value):
    """Convert a nonnegative integer-cent value safely."""
    if value is None or isinstance(value, bool):
        return None

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if (
        not amount.is_finite()
        or amount < 0
        or amount != amount.to_integral_value()
    ):
        return None

    return int(amount)


def dollars(value):
    if value is None:
        return ""
    return f"{Decimal(value) / 100:.2f}"


def fetch_catalog():
    """
    Diagnostic request for the singles lookup endpoint.

    No inventory changes are made.
    """
    log(
        "INFO",
        "Fetching public product singles catalog (GET only)"
    )

    response = requests.get(
        CATALOG_URL,
        headers={"Accept": "application/json"},
        timeout=120,
    )

    log("INFO", f"Catalog HTTP status: {response.status_code}")

    if not response.ok:
        # Do not print response headers, request headers,
        # credentials, or the full response.
        body = response.text[:1000]

        # Redact configured secrets if the server unexpectedly
        # echoes them in the error body.
        for secret_name in ("MANAPOOL_EMAIL", "MANAPOOL_TOKEN"):
            secret = os.getenv(secret_name)
            if secret:
                body = body.replace(secret, "[REDACTED]")

        log("ERROR", f"Catalog response body: {body!r}")
        response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError(
            "Unexpected catalog response: expected an object"
        )

    records = payload.get("data")

    if not isinstance(records, list):
        raise ValueError(
            "Catalog response missing a data array"
        )

    log(
        "SUCCESS",
        f"Catalog returned {len(records)} card records"
    )

    return records


def build_variant_index(catalog):
    """
    Index variants by their exact ManaPool product_id.

    Reject conflicting duplicate IDs rather than guessing.
    """
    index = {}
    duplicates = set()

    for card in catalog:
        if not isinstance(card, dict):
            continue

        variants = card.get("variants")

        if not isinstance(variants, list):
            continue

        for variant in variants:
            if not isinstance(variant, dict):
                continue

            product_id = str(
                variant.get("product_id") or ""
            ).strip()

            if not product_id:
                continue

            if product_id in index:
                duplicates.add(product_id)
                continue

            index[product_id] = {
                "card": card,
                "variant": variant,
            }

    if duplicates:
        raise ValueError(
            f"Catalog contains {len(duplicates)} duplicate "
            "variant product IDs"
        )

    log(
        "INFO",
        f"Indexed {len(index)} unique catalog variants"
    )

    return index


def generate_report(inventory, variants):
    fields = [
        "product_id",
        "card_name",
        "set_code",
        "collector_number",
        "condition",
        "finish",
        "language",
        "quantity",
        "current_price",
        "market_low",
        "target_price",
        "price_difference",
        "below_market_pct",
        "is_below_market",
        "possible_self_undercut",
        "approved",
        "verification_status",
    ]

    report_rows = []
    statuses = Counter()

    for product_id, listing in inventory.items():
        listing_quantity = quantity(listing)

        if listing_quantity == 0:
            continue

        match = variants.get(product_id)

        row = {
            "product_id": product_id,
            "card_name": "",
            "set_code": "",
            "collector_number": "",
            "condition": "",
            "finish": "",
            "language": "",
            "quantity": listing_quantity,
            "current_price": "",
            "market_low": "",
            "target_price": "",
            "price_difference": "",
            "below_market_pct": "",
            "is_below_market": False,
            "possible_self_undercut": False,
            "approved": False,
            "verification_status": "",
        }

        current_cents = cents(listing.get("price_cents"))

        if current_cents is not None:
            row["current_price"] = dollars(current_cents)

        if match is None:
            row["verification_status"] = "unmatched_product"
            statuses["unmatched_product"] += 1
            report_rows.append(row)
            continue

        card = match["card"]
        variant = match["variant"]

        row.update({
            "card_name": card.get("name") or "",
            "set_code": card.get("set_code") or "",
            "collector_number": card.get("number") or "",
            "condition": variant.get("condition_id") or "",
            "finish": variant.get("finish_id") or "",
            "language": variant.get("language_id") or "",
        })

        market_low_cents = cents(variant.get("low_price"))

        if current_cents is None:
            row["verification_status"] = "missing_current_price"
            statuses["missing_current_price"] += 1

        elif market_low_cents is None:
            row["verification_status"] = "missing_market_low"
            statuses["missing_market_low"] += 1

        elif market_low_cents == 0:
            row["verification_status"] = "zero_market_low"
            statuses["zero_market_low"] += 1

        else:
            target_cents = max(
                MIN_PRICE_CENTS,
                market_low_cents - UNDERCUT_CENTS,
            )

            difference_cents = target_cents - current_cents

            row["market_low"] = dollars(market_low_cents)
            row["target_price"] = dollars(target_cents)
            row["price_difference"] = dollars(difference_cents)

            below_market_fraction = (
                Decimal(market_low_cents - current_cents)
                / Decimal(market_low_cents)
            )

            if below_market_fraction > 0:
                row["below_market_pct"] = (
                    f"{below_market_fraction * 100:.2f}"
                )

            row["is_below_market"] = (
                market_low_cents > BELOW_MARKET_MIN_CENTS
                and below_market_fraction >= BELOW_MARKET_THRESHOLD
            )

            row["possible_self_undercut"] = (
                market_low_cents == current_cents
            )

            if row["possible_self_undercut"]:
                row["verification_status"] = (
                    "review_possible_self_undercut"
                )
                statuses["review_possible_self_undercut"] += 1
            else:
                row["verification_status"] = (
                    "pricing_preview_only"
                )
                statuses["pricing_preview_only"] += 1

        report_rows.append(row)

    with REPORT_FILE.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(report_rows)

    log(
        "SUCCESS",
        f"Generated {REPORT_FILE} with "
        f"{len(report_rows)} active listings"
    )

    for status, count in sorted(statuses.items()):
        log("INFO", f"{status}: {count}")

    log(
        "WARNING",
        "Report is for review only. No prices were updated."
    )


def main():
    log("INFO", "Starting ManaPool repricing preview")

    inventory = fetch_inventory()

    active_count = sum(
        1
        for listing in inventory.values()
        if quantity(listing) > 0
    )

    log("INFO", f"Active listings: {active_count}")
    log(
        "INFO",
        f"Zero-quantity listings excluded: "
        f"{len(inventory) - active_count}"
    )

    catalog = fetch_catalog()
    variants = build_variant_index(catalog)

    matched = sum(
        1
        for product_id, listing in inventory.items()
        if quantity(listing) > 0 and product_id in variants
    )

    log(
        "INFO",
        f"Matched active listings: {matched}/{active_count}"
    )

    generate_report(inventory, variants)

    log(
        "SUCCESS",
        "Read-only repricing preview completed"
    )


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError:
        log(
            "ERROR",
            "Preview failed: HTTPError"
        )
        sys.exit(1)
    except requests.RequestException as exc:
        log(
            "ERROR",
            f"Preview failed: {type(exc).__name__}"
        )
        sys.exit(1)
    except (
        ValueError,
        KeyError,
        RuntimeError,
        OSError,
    ) as exc:
        log("ERROR", f"Preview failed: {exc}")
        sys.exit(1)
