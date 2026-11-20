
"""Read-only ManaPool inventory integrity check; optional comparison with a local CSV."""

import argparse
import csv
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

URL = "https://manapool.com/api/v1/seller/inventory"
KEY = "product_id"


def quantity(row):
    try:
        number = Decimal(str(row.get("quantity", "")))
        if number != number.to_integral_value() or number < 0:
            raise ValueError
        return int(number)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid or missing quantity") from None


def index_rows(rows, label):
    indexed = {}
    duplicates = []

    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(
                f"{label}: non-object listing at position {i}"
            )

        identifier = str(row.get(KEY) or "").strip()

        if not identifier:
            raise ValueError(
                f"{label}: missing {KEY} at position {i}"
            )

        if identifier in indexed:
            duplicates.append(identifier)
        else:
            indexed[identifier] = row

    if duplicates:
        print(
            f"ERROR    {label}: {len(duplicates)} duplicate "
            f"{KEY} occurrences; examples: "
            f"{', '.join(duplicates[:5])}"
        )
        raise ValueError(f"{label}: duplicate product IDs")

    total_quantity = sum(
        quantity(row) for row in indexed.values()
    )

    print(
        f"SUCCESS  {label}: {len(indexed)} unique product IDs; "
        f"{total_quantity} total cards"
    )

    # NEW: Count zero-quantity inventory records.
    zero_quantity_count = sum(
        1 for row in indexed.values()
        if quantity(row) == 0
    )

    print(
        f"INFO     {label} zero-quantity listings: "
        f"{zero_quantity_count}"
    )

    return indexed


def validate_credentials():
    """Verify credential presence without logging their contents."""

    print("INFO     Validating ManaPool credentials", flush=True)

    missing = []

    for name in ("MANAPOOL_EMAIL", "MANAPOOL_TOKEN"):
        if not (os.getenv(name) or "").strip():
            print(
                f"ERROR    {name} is missing or empty",
                flush=True
            )
            missing.append(name)
        else:
            print(
                f"SUCCESS  {name} is configured",
                flush=True
            )

    if missing:
        raise RuntimeError(
            "Credential validation failed; API request not attempted"
        )


def fetch_inventory():
    validate_credentials()

    email = os.environ["MANAPOOL_EMAIL"]
    token = os.environ["MANAPOOL_TOKEN"]

    session = requests.Session()

    session.headers.update({
        "X-ManaPool-Email": email,
        "X-ManaPool-Access-Token": token
    })

    cursor = None
    cursors = set()
    records = []
    expected_total = None
    page = 0

    while True:
        params = {"limit": 1000}

        if cursor is not None:
            params["cursor"] = cursor

        response = session.get(
            URL,
            params=params,
            timeout=45
        )

        response.raise_for_status()

        payload = response.json()

        items = payload["inventory"]
        pagination = payload["pagination"]

        if not isinstance(items, list) or not isinstance(
            pagination, dict
        ):
            raise ValueError(
                "Unexpected API response structure"
            )

        reported = pagination.get("total")

        if reported is not None:
            try:
                reported = int(reported)
            except (TypeError, ValueError):
                raise ValueError(
                    "Invalid API pagination total"
                ) from None

            if expected_total is None:
                expected_total = reported
            elif reported != expected_total:
                raise ValueError(
                    "API pagination total changed during retrieval"
                )

        records.extend(items)
        page += 1

        print(
            f"INFO     Page {page}: "
            f"{len(items)} listings received"
        )

        next_cursor = pagination.get("next_cursor")

        if not next_cursor:
            break

        if not items:
            raise ValueError(
                "Empty page with a next cursor"
            )

        if next_cursor == cursor or next_cursor in cursors:
            raise ValueError(
                "Repeated pagination cursor"
            )

        cursors.add(next_cursor)
        cursor = next_cursor

    print(
        f"INFO     API reported total: "
        f"{expected_total if expected_total is not None else 'not supplied'}"
    )

    print(
        f"INFO     Fetched rows: "
        f"{len(records)} across {page} pages"
    )

    if (
        expected_total is not None
        and len(records) != expected_total
    ):
        raise ValueError(
            f"API count mismatch: fetched={len(records)} "
            f"reported={expected_total}"
        )

    return index_rows(records, "API")


def read_csv(path):
    with Path(path).open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        reader = csv.DictReader(f)

        if (
            not reader.fieldnames
            or KEY not in reader.fieldnames
            or "quantity" not in reader.fieldnames
        ):
            raise ValueError(
                "CSV must contain product_id and quantity columns"
            )

        return index_rows(list(reader), "CSV")


def compare(api, exported):
    only_api = sorted(api.keys() - exported.keys())
    only_csv = sorted(exported.keys() - api.keys())

    quantity_mismatches = []

    for key in api.keys() & exported.keys():
        a = quantity(api[key])
        b = quantity(exported[key])

        if a != b:
            quantity_mismatches.append((key, a, b))

    print(
        f"INFO     API-only product IDs: {len(only_api)}"
    )

    print(
        f"INFO     CSV-only product IDs: {len(only_csv)}"
    )

    print(
        "INFO     Quantity mismatches on matching IDs: "
        f"{len(quantity_mismatches)}"
    )

    # NEW: Check the quantities of API-only records.
    api_only_zero = [
        key for key in only_api
        if quantity(api[key]) == 0
    ]

    api_only_nonzero = [
        key for key in only_api
        if quantity(api[key]) > 0
    ]

    print(
        "INFO     API-only zero-quantity listings: "
        f"{len(api_only_zero)}"
    )

    print(
        "INFO     API-only nonzero-quantity listings: "
        f"{len(api_only_nonzero)}"
    )

    for key in only_api[:10]:
        row = api[key]
        print(
            f"WARNING  API-only: "
            f"{row.get('name', 'unknown')} | "
            f"product_id={key} | "
            f"quantity={quantity(row)}"
        )

    for key in only_csv[:10]:
        row = exported[key]
        print(
            f"WARNING  CSV-only: "
            f"{row.get('name', 'unknown')} | "
            f"product_id={key}"
        )

    for key, a, b in sorted(quantity_mismatches)[:10]:
        row = exported[key]
        print(
            f"WARNING  Quantity differs: "
            f"{row.get('name', 'unknown')} | "
            f"product_id={key} | "
            f"API={a} CSV={b}"
        )

    # NEW: Treat zero-quantity API-only records as an
    # explained difference, rather than a reconciliation failure.
    if (
        only_api
        and not api_only_nonzero
        and not only_csv
        and not quantity_mismatches
    ):
        print(
            "SUCCESS  Inventory discrepancy explained: "
            "all API-only listings have zero quantity"
        )
        print(
            "SUCCESS  Active inventory IDs and quantities match"
        )
        return

    if only_api or only_csv or quantity_mismatches:
        raise ValueError(
            "Inventory reconciliation failed; no changes attempted"
        )

    print(
        "SUCCESS  API and CSV listing identities "
        "and quantities match"
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--compare-csv",
        type=Path,
        help="Local ManaPool CSV export to reconcile against live API"
    )

    args = parser.parse_args()

    api = fetch_inventory()

    if args.compare_csv:
        compare(api, read_csv(args.compare_csv))
    else:
        print(
            "INFO     CSV comparison skipped "
            "(no --compare-csv supplied)"
        )

    print(
        "SUCCESS  Read-only inventory validation completed"
    )


if __name__ == "__main__":
    try:
        main()
    except (
        requests.RequestException,
        ValueError,
        KeyError,
        RuntimeError,
        OSError
    ) as exc:
        # Never print HTTP exception bodies or request headers.
        if isinstance(exc, requests.RequestException):
            print(
                f"ERROR    API request failed: "
                f"{type(exc).__name__}",
                file=sys.stderr
            )
        else:
            print(f"ERROR    {exc}", file=sys.stderr)

        sys.exit(1)
