"""One-time, manually triggered ManaPool price-only pilot (MAX 25 listings).

Requires environment variable PILOT_CONFIRM=CONFIRM_25_PRICE_UPDATES.
Uses the latest preview generated IN THIS RUN, then refetches inventory and
checks unchanged prices/quantities before any write. Writes price_cents only;
quantity is explicitly null. Stops immediately on unexpected API responses.
"""
import csv
import os
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import requests

from inventory import fetch_inventory, quantity
from repricing_preview import (
    MIN_PRICE_CENTS, fetch_market_prices, generate_report, parse_cents,
    proposed_price,
)

BASE_URL = "https://manapool.com/api/v1"
REPORT = Path("repricing_report.csv")
JOURNAL = Path("pilot_25_results.csv")
LIMIT = 25
CONFIRM = "CONFIRM_25_PRICE_UPDATES"
FIELDS = ["product_id", "card_name", "action", "old_price", "target_price",
          "quantity_before", "quantity_after", "status", "details"]


def log(level, message):
    print(f"{level:<8} {message}", flush=True)


def money_to_cents(value):
    try:
        d = Decimal(str(value)) * 100
        if not d.is_finite() or d != d.to_integral_value():
            return None
        return int(d)
    except Exception:
        return None


def write_result(writer, handle, **values):
    writer.writerow({field: values.get(field, "") for field in FIELDS})
    handle.flush()


def main():
    if os.environ.get("PILOT_CONFIRM") != CONFIRM:
        raise RuntimeError("Pilot confirmation missing. No changes made.")
    email = os.environ.get("MANAPOOL_EMAIL")
    token = os.environ.get("MANAPOOL_TOKEN")
    if not email or not token:
        raise RuntimeError("MANAPOOL_EMAIL and MANAPOOL_TOKEN must be configured")
    if REPORT.exists():
        REPORT.unlink()  # never reuse an older report

    log("INFO", "Generating fresh pricing report for the pilot")
    inventory = fetch_inventory()
    active = {pid: item for pid, item in inventory.items() if quantity(item) > 0}
    variants = fetch_market_prices(active)
    generate_report(active, variants)

    candidates = []
    with REPORT.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row["action"] not in {"floor_increase", "competitive_decrease"}:
                continue
            pid = row["product_id"]
            try:
                UUID(pid)
            except (ValueError, AttributeError):
                continue
            old = money_to_cents(row["current_price"])
            target = money_to_cents(row["target_price"])
            market = money_to_cents(row["market_low"]) if row["market_low"] else None
            if old is None or target is None or target < MIN_PRICE_CENTS or old == target:
                continue
            if row["action"] == "floor_increase":
                if not (old < MIN_PRICE_CENTS and target == MIN_PRICE_CENTS):
                    continue
            elif not (target < old and market is not None and market > 0
                      and old > market and target == max(MIN_PRICE_CENTS, market + 1)):
                continue
            if row["verification_status"] not in {"floor_increase", "one_cent_above_market_low"}:
                continue
            candidates.append((row, old, target))

    # Pilot starts with the first 25 deterministic eligible listings. No other writes.
    selected = candidates[:LIMIT]
    if not selected:
        raise RuntimeError("No eligible price changes found; nothing to update")
    log("INFO", f"Eligible: {len(candidates)}; selected for pilot: {len(selected)} (maximum {LIMIT})")
    log("INFO", "Refetching seller inventory to reject stale changes")
    fresh = fetch_inventory()
    for row, old, target in selected:
        current = fresh.get(row["product_id"])
        if current is None or quantity(current) != int(row["quantity"]) or parse_cents(current.get("price_cents")) != old:
            raise RuntimeError(f"Preflight failed for {row['product_id']}; no writes attempted")
        if quantity(current) <= 0:
            raise RuntimeError(f"Inactive listing in pilot: {row['product_id']}")

    session = requests.Session()
    session.headers.update({
        "X-ManaPool-Email": email,
        "X-ManaPool-Access-Token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    successes = 0
    with JOURNAL.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        handle.flush()
        for index, (row, old, target) in enumerate(selected, 1):
            pid = row["product_id"]
            before_qty = int(row["quantity"])
            log("INFO", f"Pilot update {index}/{len(selected)}: {pid} {old}c -> {target}c")
            try:
                response = session.put(
                    f"{BASE_URL}/seller/inventory/product/mtg_single/{pid}",
                    json={"price_cents": target, "quantity": None},
                    timeout=30,
                )
            except requests.RequestException as exc:
                write_result(writer, handle, product_id=pid, card_name=row["card_name"],
                             action=row["action"], old_price=old, target_price=target,
                             quantity_before=before_qty, status="UNKNOWN_AFTER_NETWORK_ERROR",
                             details=type(exc).__name__)
                raise RuntimeError("Network failure: update may have succeeded. STOP; inspect ManaPool and journal") from exc
            if response.status_code != 200:
                write_result(writer, handle, product_id=pid, card_name=row["card_name"],
                             action=row["action"], old_price=old, target_price=target,
                             quantity_before=before_qty, status="HTTP_ERROR",
                             details=f"HTTP {response.status_code}")
                raise RuntimeError(f"HTTP {response.status_code} on product {pid}; stopping")
            try:
                payload = response.json()
                result = payload["inventory"]
                returned_price = parse_cents(result["price_cents"])
                returned_qty = result["quantity"]
                returned_pid = result["product_id"]
                valid = (returned_pid == pid and returned_price == target
                         and type(returned_qty) is int and returned_qty == before_qty)
            except (ValueError, TypeError, KeyError):
                valid = False
                returned_qty = ""
            write_result(writer, handle, product_id=pid, card_name=row["card_name"],
                         action=row["action"], old_price=old, target_price=target,
                         quantity_before=before_qty, quantity_after=returned_qty,
                         status="VERIFIED_RESPONSE" if valid else "VERIFY_FAILED",
                         details="")
            if not valid:
                raise RuntimeError(f"Unexpected response for {pid}; STOP and inspect inventory")
            successes += 1
            time.sleep(0.5)
    log("SUCCESS", f"Pilot completed: {successes} verified price-only responses")
    log("WARNING", "Verify these 25 listings in ManaPool before expanding the rollout")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc)
        for secret in (os.getenv("MANAPOOL_EMAIL"), os.getenv("MANAPOOL_TOKEN")):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        log("ERROR", f"{type(exc).__name__}: {message}")
        sys.exit(1)
