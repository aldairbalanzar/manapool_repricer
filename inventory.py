"""Read-only ManaPool inventory connectivity test."""
import os
import sys

import requests

URL = "https://manapool.com/api/v1/seller/inventory"


def main() -> None:
    email = os.environ.get("MANAPOOL_EMAIL")
    token = os.environ.get("MANAPOOL_TOKEN")
    if not email or not token:
        raise RuntimeError("Missing MANAPOOL_EMAIL or MANAPOOL_TOKEN secret")

    session = requests.Session()
    session.headers.update({
        "X-ManaPool-Email": email,
        "X-ManaPool-Access-Token": token,
    })
    inventory_count = 0
    page_count = 0
    cursor = None
    seen_cursors = set()
    expected_total = None

    while True:
        params = {"limit": 1000}
        if cursor is not None:
            params["cursor"] = cursor
        response = session.get(URL, params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        items = payload["inventory"]
        pagination = payload["pagination"]
        if not isinstance(items, list):
            raise ValueError("Unexpected inventory response")
        if expected_total is None:
            expected_total = pagination.get("total")
        inventory_count += len(items)
        page_count += 1
        print(f"INFO     Page {page_count}: {len(items)} listings received")
        next_cursor = pagination.get("next_cursor")
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            raise RuntimeError("Repeated pagination cursor detected")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    if expected_total is not None and inventory_count != expected_total:
        raise RuntimeError(
            f"Inventory count mismatch: fetched={inventory_count}, reported={expected_total}"
        )
    print(f"SUCCESS  Retrieved {inventory_count} listings across {page_count} pages")
    print("SUCCESS  Read-only test completed; no inventory updates attempted")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
        print(f"ERROR    Inventory test failed: {type(exc).__name__}", file=sys.stderr)
        # Avoid dumping response bodies or exception text that might contain private data.
        sys.exit(1)
