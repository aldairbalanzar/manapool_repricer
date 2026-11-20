# ManaPool automatic repricing — safe staging

This package schedules the ManaPool pricing preview with the one-cent-above-low strategy every day at **8 PM Eastern**, including daylight-saving time changes. It does **not** yet write prices. ManaPool's price-update endpoint and schema must be verified before live mode is implemented.

## Files

- `repricing_preview.py` — full read-only pricing calculation and report generator
- `scheduled_repricing.py` — schedule-time guard and strict CSV safety validation
- `.github/workflows/scheduled-repricing.yml` — nightly GitHub Actions workflow

Keep your existing `inventory.py` in the repository. It is imported by `repricing_preview.py`.

## Installation

Copy the two Python files into your repository root, and copy the workflow into `.github/workflows/`. Commit and push. Your existing GitHub secrets `MANAPOOL_EMAIL` and `MANAPOOL_TOKEN` are used. The schedule uses UTC 00:00 and 01:00 triggers with an America/New_York time guard, so only the 8 PM Eastern run proceeds. GitHub scheduled workflows can run late.

Run manually with `python scheduled_repricing.py` or through GitHub Actions > ManaPool Scheduled Repricing (Safe Preview) > Run workflow.

## Pricing rules

- If current price is below $0.75, target $0.75.
- If current price is greater than the reported market low, target `max($0.75, market low + $0.01)`; if this equals current price, leave it unchanged.
- If current price is equal to or below the reported market low, leave it unchanged (unless below the floor). This avoids repeatedly raising your own lowest listing.
- Missing market data is flagged for review, except for the floor correction.
- ManaPool market low may include your own listing; this does not identify the lowest competing seller.

## Safety

- Zero-quantity listings are excluded.
- All proposed targets must be at least $0.75.
- Price increases and decreases must move in the expected direction.
- Missing/duplicate product IDs cause the run to fail.
- No quantity writes exist.
- Setting `REPRICING_MODE=live` fails immediately and never sends a write request.

## To enable live automation

Provide the official ManaPool **seller price-update endpoint documentation**, including HTTP method, request body, authentication, whether quantity is optional, rate limits, and response shape. The write implementation must re-fetch current inventory, verify each listing's current price and quantity, limit per-run decreases, apply price-only writes in small batches, and verify each result before enabling the scheduled live mode.
