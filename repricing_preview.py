
"""
ManaPool read-only repricing preview.

Fetches active seller inventory, looks up matching product variants,
and generates repricing_report.csv.

This script NEVER updates prices, quantities, or inventory records.
"""

import csv
import os
import sys
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from inventory import fetch_inventory, quantity


PRODUCTS_URL = "https://manapool.com/api/v1/products/singles"
REPORT_FILE = Path("repricing_report.csv")

BATCH_SIZE = 100
REQUEST_TIMEOUT = 120
REQUEST_DELAY_SECONDS = 0.25

MIN_PRICE_CENTS = 75
UNDERCUT_CENTS = 1

BELOW_MARKET_THRESHOLD = Decimal("0.20")
BELOW_MARKET_MIN_CENTS = 500

# ManaPool defaults to English if languages is omitted.
# Request all documented languages so non-English cards are included.
LANGUAGES = [
    "EN", "JA", "FR", "IT", "DE", "ES",
    "AR", "CS", "CT", "EL", "HE", "KO",
    "LA", "PH", "PT", "RU", "SA",
    "QYA", "DW",
]

REPORT_FIELDS = [
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


def log(level, message):
    print(f"{level:<8} {message}", flush=True)


def parse_cents(value):
    """Validate integer cents without floating-point rounding."""
    if value is None or isinstance(value, bool):
        return None

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if not amount.is_finite():
        return None

    if amount < 0:
        return None

    if amount != amount.to_integral_value():
        return None

    return int(amount)


def dollars(value):
    if value is None:
        return ""

    return f"{Decimal(value) / 100:.2f}"


def redact(text):
    """Remove configured credentials from diagnostic output."""
    result = str(text)

    for key in ("MANAPOOL_EMAIL", "MANAPOOL_TOKEN"):
        secret = os.getenv(key)

        if secret:
            result = result.replace(secret, "[REDACTED]")

    return result


def fetch_product_batch(session, product_ids, batch_number):
    """
    Fetch up to 100 product IDs using the documented filter.

    Requests are GET-only.
    """

    params = []

    for product_id in product_ids:
        params.append(("product_ids", product_id))

    for language in LANGUAGES:
        params.append(("languages", language))

    response = session.get(
        PRODUCTS_URL,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        log(
            "ERROR",
            f"Catalog batch {batch_number}: "
            f"HTTP {response.status_code}",
        )

        # Only print a short, redacted error response.
        body = redact(response.text[:1000])

        log("ERROR", f"Catalog response: {body!r}")
        response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError(
            f"Batch {batch_number}: unexpected response structure"
        )

    records = payload.get("data")

    if not isinstance(records, list):
        raise ValueError(
            f"Batch {batch_number}: missing data array"
        )

    meta = payload.get("meta", {})

    if not isinstance(meta, dict):
        raise ValueError(
            f"Batch {batch_number}: invalid metadata"
        )

    return records, meta.get("as_of")


def fetch_market_prices(active_inventory):
    """
    Look up active inventory product IDs in batches.

    Index exact variants using variants[].product_id.
    """

    product_ids = sorted(active_inventory.keys())

    total_batches = (
        len(product_ids) + BATCH_SIZE - 1
    ) // BATCH_SIZE

    log(
        "INFO",
        f"Fetching prices for {len(product_ids)} "
        f"active product IDs",
    )

    log(
        "INFO",
        f"Expected catalog requests: {total_batches}",
    )

    session = requests.Session()

    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "ManaPool-Repricing-Preview/1.0",
    })

    variant_index = {}
    duplicate_ids = set()
    returned_cards = 0
    as_of_values = set()

    for start in range(0, len(product_ids), BATCH_SIZE):
        batch = product_ids[start:start + BATCH_SIZE]
        batch_number = start // BATCH_SIZE + 1

        records, as_of = fetch_product_batch(
            session,
            batch,
            batch_number,
        )

        if as_of:
            as_of_values.add(as_of)

        returned_cards += len(records)

        requested_ids = set(batch)
        matched_in_batch = set()

        for card in records:
            if not isinstance(card, dict):
                raise ValueError(
                    f"Batch {batch_number}: invalid card object"
                )

            variants = card.get("variants")

            if not isinstance(variants, list):
                raise ValueError(
                    f"Batch {batch_number}: missing variants array"
                )

            for variant in variants:
                if not isinstance(variant, dict):
                    raise ValueError(
                        f"Batch {batch_number}: invalid variant"
                    )

                product_id = str(
                    variant.get("product_id") or ""
                ).strip()

                if product_id not in requested_ids:
                    continue

                if (
                    product_id in variant_index
                    or product_id in matched_in_batch
                ):
                    duplicate_ids.add(product_id)
                    continue

                matched_in_batch.add(product_id)

                variant_index[product_id] = {
                    "card": card,
                    "variant": variant,
                }

        log(
            "INFO",
            f"Catalog batch {batch_number}/{total_batches}: "
            f"requested={len(batch)}, "
            f"cards={len(records)}, "
            f"matched={len(matched_in_batch)}",
        )

        if batch_number < total_batches:
            time.sleep(REQUEST_DELAY_SECONDS)

    if duplicate_ids:
        raise ValueError(
            f"Catalog returned {len(duplicate_ids)} "
            "duplicate matching product IDs"
        )

    log(
        "SUCCESS",
        f"Catalog lookup completed: "
        f"{returned_cards} card records received",
    )

    log(
        "SUCCESS",
        f"Matched {len(variant_index)}/"
        f"{len(product_ids)} active product IDs",
    )

    if as_of_values:
        log(
            "INFO",
            f"Catalog snapshot timestamps: "
            f"{len(as_of_values)} distinct",
        )

        log(
            "INFO",
            f"Latest catalog as_of: {max(as_of_values)}",
        )
    else:
        log(
            "WARNING",
            "Catalog did not provide an as_of timestamp",
        )

    return variant_index


def generate_report(active_inventory, variant_index):
    rows = []
    statuses = Counter()

    for product_id in sorted(active_inventory):
        listing = active_inventory[product_id]
        listing_quantity = quantity(listing)

        current_cents = parse_cents(
            listing.get("price_cents")
        )

        row = {
            "product_id": product_id,
            "card_name": "",
            "set_code": "",
            "collector_number": "",
            "condition": "",
            "finish": "",
            "language": "",
            "quantity": listing_quantity,
            "current_price": dollars(current_cents),
            "market_low": "",
            "target_price": "",
            "price_difference": "",
            "below_market_pct": "",
            "is_below_market": False,
            "possible_self_undercut": False,
            "approved": False,
            "verification_status": "",
        }

        match = variant_index.get(product_id)

        if match is None:
            status = "unmatched_product"

        else:
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

            market_low_cents = parse_cents(
                variant.get("low_price")
            )

            available_quantity = variant.get(
                "available_quantity"
            )

            if current_cents is None:
                status = "missing_current_price"

            elif market_low_cents is None:
                status = "missing_market_low"

            elif market_low_cents == 0:
                status = "zero_market_low"

            elif (
                not isinstance(available_quantity, (int, float))
                or isinstance(available_quantity, bool)
                or available_quantity <= 0
            ):
                status = "no_market_stock"

            else:
                target_cents = max(
                    MIN_PRICE_CENTS,
                    market_low_cents - UNDERCUT_CENTS,
                )

                difference_cents = (
                    target_cents - current_cents
                )

                row["market_low"] = dollars(
                    market_low_cents
                )

                row["target_price"] = dollars(
                    target_cents
                )

                row["price_difference"] = dollars(
                    difference_cents
                )

                below_fraction = (
                    Decimal(market_low_cents - current_cents)
                    / Decimal(market_low_cents)
                )

                if below_fraction > 0:
                    row["below_market_pct"] = (
                        f"{below_fraction * 100:.2f}"
                    )

                row["is_below_market"] = (
                    market_low_cents > BELOW_MARKET_MIN_CENTS
                    and below_fraction >= BELOW_MARKET_THRESHOLD
                )

                row["possible_self_undercut"] = (
                    market_low_cents == current_cents
                )

                if row["possible_self_undercut"]:
                    status = "review_possible_self_undercut"
                else:
                    status = "pricing_preview_only"

        row["verification_status"] = status
        statuses[status] += 1
        rows.append(row)

    with REPORT_FILE.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=REPORT_FIELDS,
        )

        writer.writeheader()
        writer.writerows(rows)

    log(
        "SUCCESS",
        f"Generated {REPORT_FILE} "
        f"with {len(rows)} active listings",
    )

    for status, count in sorted(statuses.items()):
        log("INFO", f"{status}: {count}")

    below_market_count = sum(
        1 for row in rows
        if row["is_below_market"]
    )

    self_undercut_count = sum(
        1 for row in rows
        if row["possible_self_undercut"]
    )

    log(
        "INFO",
        f"Listings flagged below market: "
        f"{below_market_count}",
    )

    log(
        "INFO",
        f"Possible self-undercutting listings: "
        f"{self_undercut_count}",
    )

    log(
        "WARNING",
        "All proposed prices require review. "
        "No inventory changes were made.",
    )


def main():
    log(
        "INFO",
        "Starting ManaPool repricing preview",
    )

    # Uses our previously validated, read-only inventory code.
    inventory = fetch_inventory()

    active_inventory = {
        product_id: listing
        for product_id, listing in inventory.items()
        if quantity(listing) > 0
    }

    zero_quantity_count = (
        len(inventory) - len(active_inventory)
    )

    log(
        "INFO",
        f"Active listings: {len(active_inventory)}",
    )

    log(
        "INFO",
        f"Zero-quantity listings excluded: "
        f"{zero_quantity_count}",
    )

    if not active_inventory:
        raise ValueError(
            "No active listings found; report not generated"
        )

    variant_index = fetch_market_prices(
        active_inventory
    )

    generate_report(
        active_inventory,
        variant_index,
    )

    log(
        "SUCCESS",
        "Read-only repricing preview completed",
    )


if __name__ == "__main__":
    try:
        main()

    except requests.HTTPError:
        log(
            "ERROR",
            "Preview failed: HTTPError",
        )
        sys.exit(1)

    except requests.RequestException as exc:
        log(
            "ERROR",
            f"Preview failed: {type(exc).__name__}",
        )
        sys.exit(1)

    except (
        ValueError,
        KeyError,
        RuntimeError,
        OSError,
    ) as exc:
        log(
            "ERROR",
            f"Preview failed: {redact(exc)}",
        )
        sys.exit(1)
