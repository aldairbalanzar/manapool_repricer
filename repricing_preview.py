"""Read-only ManaPool repricing report: $0.01 above low, configurable floor, no writes."""

import csv
import os
import sys
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from inventory import fetch_inventory, quantity
from repricing_exceptions import load_exceptions, listing_identity

PRODUCTS_URL = "https://manapool.com/api/v1/products/singles"
REPORT_FILE = Path("repricing_report.csv")
BATCH_SIZE = 100
REQUEST_TIMEOUT = 120
REQUEST_DELAY_SECONDS = 0.25
def read_min_price_cents():
    raw = os.getenv("MANAPOOL_MIN_PRICE_CENTS", "75")
    if not raw.isascii() or not raw.isdecimal() or int(raw) <= 0:
        raise ValueError("MANAPOOL_MIN_PRICE_CENTS must be a positive integer in cents")
    return int(raw)


MIN_PRICE_CENTS = read_min_price_cents()
ABOVE_LOW_CENTS = 1
BELOW_MARKET_THRESHOLD = Decimal("0.20")
BELOW_MARKET_MIN_CENTS = 500
LANGUAGES = [
    "EN", "JA", "FR", "IT", "DE", "ES", "AR", "CS", "CT", "EL",
    "HE", "KO", "LA", "PH", "PT", "RU", "SA", "QYA", "DW",
]
REPORT_FIELDS = [
    "product_id", "scryfall_id", "is_foil", "is_repricing_exception", "exception_reason", "card_name", "set_code", "collector_number", "condition",
    "finish", "language", "quantity", "current_price", "market_low",
    "target_price", "price_difference", "below_market_pct", "is_below_market",
    "possible_self_undercut", "approved", "action", "verification_status",
]


def log(level, message):
    print(f"{level:<8} {message}", flush=True)


def parse_cents(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
        return None
    return int(amount)


def dollars(value):
    return "" if value is None else f"{Decimal(value) / 100:.2f}"


def redact(value):
    result = str(value)
    for name in ("MANAPOOL_EMAIL", "MANAPOOL_TOKEN"):
        secret = os.getenv(name)
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result


def fetch_product_batch(session, product_ids, batch_number):
    # Repeated query parameters are the documented array filter form.
    params = [("product_ids", product_id) for product_id in product_ids]
    params.extend(("languages", language) for language in LANGUAGES)
    response = session.get(PRODUCTS_URL, params=params, timeout=REQUEST_TIMEOUT)
    if not response.ok:
        log("ERROR", f"Catalog batch {batch_number}: HTTP {response.status_code}")
        log("ERROR", f"Catalog response: {redact(response.text[:1000])!r}")
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError(f"Batch {batch_number}: unexpected catalog response")
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError(f"Batch {batch_number}: invalid metadata")
    return payload["data"], meta.get("as_of")


def fetch_market_prices(active_inventory):
    product_ids = sorted(active_inventory)
    total_batches = (len(product_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    log("INFO", f"Fetching prices for {len(product_ids)} active product IDs")
    log("INFO", f"Expected catalog requests: {total_batches}")
    index = {}
    timestamps = set()
    returned_cards = 0
    with requests.Session() as session:
        session.headers.update({
            "Accept": "application/json",
            "User-Agent": "ManaPool-Repricing-Preview/1.0",
        })
        for start in range(0, len(product_ids), BATCH_SIZE):
            batch = product_ids[start:start + BATCH_SIZE]
            batch_number = start // BATCH_SIZE + 1
            cards, as_of = fetch_product_batch(session, batch, batch_number)
            returned_cards += len(cards)
            if as_of:
                timestamps.add(as_of)
            requested = set(batch)
            matched = set()
            for card in cards:
                if not isinstance(card, dict) or not isinstance(card.get("variants"), list):
                    raise ValueError(f"Batch {batch_number}: invalid card/variants")
                for variant in card["variants"]:
                    if not isinstance(variant, dict):
                        raise ValueError(f"Batch {batch_number}: invalid variant")
                    product_id = str(variant.get("product_id") or "").strip()
                    if product_id not in requested:
                        continue
                    if product_id in index:
                        raise ValueError(f"Duplicate matching product ID: {product_id}")
                    index[product_id] = {"card": card, "variant": variant}
                    matched.add(product_id)
            log("INFO", f"Catalog batch {batch_number}/{total_batches}: "
                f"requested={len(batch)}, cards={len(cards)}, matched={len(matched)}")
            if batch_number < total_batches:
                time.sleep(REQUEST_DELAY_SECONDS)
    log("SUCCESS", f"Catalog lookup completed: {returned_cards} card records received")
    log("SUCCESS", f"Matched {len(index)}/{len(product_ids)} active product IDs")
    if timestamps:
        log("INFO", f"Catalog snapshot timestamps: {len(timestamps)} distinct")
        log("INFO", f"Latest catalog as_of: {max(timestamps)}")
    else:
        log("WARNING", "Catalog did not provide an as_of timestamp")
    return index


def proposed_price(current_cents, market_low_cents):
    """Target 1 cent ABOVE the reported low, without chasing our own price.

    Never change an at/below-low listing except to enforce the configurable floor.
    The reported low may include our own listing; it is not a competitor-only low.
    """
    if current_cents is None:
        return None, "review", "missing_current_price"
    if current_cents < MIN_PRICE_CENTS:
        return MIN_PRICE_CENTS, "floor_increase", "floor_increase"
    if market_low_cents is None:
        return current_cents, "review", "missing_market_low"
    if market_low_cents == 0:
        return current_cents, "review", "zero_market_low"
    if current_cents <= market_low_cents:
        return current_cents, "unchanged", "already_at_or_below_market_low"
    target = max(MIN_PRICE_CENTS, market_low_cents + ABOVE_LOW_CENTS)
    if target < current_cents:
        return target, "competitive_decrease", "one_cent_above_market_low"
    return current_cents, "unchanged", "already_one_cent_above_market_low"


def generate_report(active_inventory, variant_index, exceptions=None):
    if exceptions is None:
        exceptions = load_exceptions()
    log("INFO", f"Loaded {len(exceptions)} repricing exceptions")
    rows = []
    actions = Counter()
    statuses = Counter()
    for product_id in sorted(active_inventory):
        listing = active_inventory[product_id]
        current = parse_cents(listing.get("price_cents"))
        match = variant_index.get(product_id)
        card = match["card"] if match else {}
        variant = match["variant"] if match else {}
        market = parse_cents(variant.get("low_price")) if match else None
        available = variant.get("available_quantity") if match else None
        market_valid = market is not None and market > 0
        below_fraction = (
            Decimal(market - current) / Decimal(market)
            if market_valid and current is not None else None
        )
        below_flag = (
            below_fraction is not None
            and market > BELOW_MARKET_MIN_CENTS
            and below_fraction >= BELOW_MARKET_THRESHOLD
        )
        # If the catalog has no stock, do not infer a competitive market price.
        if match and (
            isinstance(available, bool)
            or not isinstance(available, (int, float))
            or available <= 0
        ):
            target, action, status = proposed_price(current, None)
            if action != "floor_increase":
                action, status = "review", "no_market_stock"
        else:
            target, action, status = proposed_price(current, market)
        if not match:
            status = "unmatched_product_floor_increase" if action == "floor_increase" else "unmatched_product"
            if action != "floor_increase":
                action = "review"
        key = None
        try:
            key = listing_identity(listing, card, variant)
        except (ValueError, TypeError, AttributeError):
            if exceptions:
                target, action, status = current, "review", "exception_identity_unverified"
        excluded = key in exceptions
        if excluded:
            target, action, status = current, "exception", "repricing_exception"
        elif key and any(k[0] == key[0] and k[3] == key[3] for k in exceptions):
            target, action, status = current, "review", "exception_metadata_conflict"
        # A low-price tie might include our own listing; no price change is proposed.
        possible_self_undercut = market_valid and current == market
        row = {
            "product_id": product_id,
            "scryfall_id": key[0] if key else "",
            "is_foil": key[3] if key else "",
            "is_repricing_exception": excluded,
            "exception_reason": exceptions[key] if excluded else "",
            "card_name": card.get("name") or "",
            "set_code": card.get("set_code") or "",
            "collector_number": card.get("number") or "",
            "condition": variant.get("condition_id") or "",
            "finish": variant.get("finish_id") or "",
            "language": variant.get("language_id") or "",
            "quantity": quantity(listing),
            "current_price": dollars(current),
            "market_low": dollars(market) if market_valid else "",
            "target_price": dollars(target),
            "price_difference": dollars(target - current) if target is not None and current is not None else "",
            "below_market_pct": f"{below_fraction * 100:.2f}" if below_fraction is not None and below_fraction > 0 else "",
            "is_below_market": below_flag,
            "possible_self_undercut": possible_self_undercut,
            "approved": False,
            "action": action,
            "verification_status": status,
        }
        rows.append(row)
        actions[action] += 1
        statuses[status] += 1
    with REPORT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    log("INFO", f"Listings excluded by exceptions: {actions['exception']}")
    log("SUCCESS", f"Generated {REPORT_FILE} with {len(rows)} active listings")
    for action, count in sorted(actions.items()):
        log("INFO", f"Action {action}: {count}")
    for status, count in sorted(statuses.items()):
        log("INFO", f"Status {status}: {count}")
    log("INFO", f"Listings flagged 20%+ below market (> $5): "
        f"{sum(bool(row['is_below_market']) for row in rows)}")
    log("INFO", f"Listings tied with market low: "
        f"{sum(bool(row['possible_self_undercut']) for row in rows)}")
    log("WARNING", "Preview only: no inventory changes were made; approved=False for every row")


def main():
    log("INFO", "Starting ManaPool repricing preview")
    log("INFO", f"Minimum listing price: {MIN_PRICE_CENTS} cents (${dollars(MIN_PRICE_CENTS)})")
    inventory = fetch_inventory()
    active = {
        product_id: listing
        for product_id, listing in inventory.items()
        if quantity(listing) > 0
    }
    log("INFO", f"Active listings: {len(active)}")
    log("INFO", f"Zero-quantity listings excluded: {len(inventory) - len(active)}")
    if not active:
        raise ValueError("No active listings found; report not generated")
    variants = fetch_market_prices(active)
    generate_report(active, variants)
    log("SUCCESS", "Read-only repricing preview completed")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError:
        log("ERROR", "Preview failed: HTTPError")
        sys.exit(1)
    except requests.RequestException as exc:
        log("ERROR", f"Preview failed: {type(exc).__name__}")
        sys.exit(1)
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        log("ERROR", f"Preview failed: {redact(exc)}")
        sys.exit(1)
