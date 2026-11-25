# ManaPool nightly live repricer

This package replaces the old **read-only scheduled workflow** with an **opt-in** live workflow. It retains the existing `inventory.py` from your repository (DO NOT remove or replace it). It uses `requests` and Python 3.12.

## Pricing rules

- Only active `mtg_single` listings with positive quantity.
- Raise listings below the configured minimum to that minimum (default $0.75).
- If above the reported market low, decrease to $0.01 **above** market low, never below the configured minimum.
- If already at or below market low, leave unchanged (except floor).
- The reported low can include your own listing. This is **not** a competitor-only price.
- Write ONLY `{ "price_cents": target, "quantity": null }`. Never modify quantities.
- Bulk POST requests of up to 1000 listings, up to 10,000 eligible changes per run. Stop on API errors or verification mismatches. Skip stale listings detected during periodic inventory refreshes.

## Install

Copy `full_repricing.py`, `account_visibility.py`, `repricing_preview.py` and `.github/workflows/` into the root of your existing `manapool_repricer` repo. Keep your `inventory.py` and existing GitHub secrets `MANAPOOL_EMAIL`, `MANAPOOL_TOKEN`. The token must have inventory-write and seller-settings-write permissions. **Delete/disable the older pilot-25 workflow** to avoid accidentally running it.

Commit and push to `main`. Merely pushing does **not** enable nightly live updates. The scheduled workflow will run but reject live writes until enabled.

### First full run (manual)

GitHub Actions → **ManaPool Scheduled Repricing (LIVE - Offline Guard)** → Run workflow → `main` → confirmation `CONFIRM_FULL_REPRICING`.

This will update **all eligible listings**, not just 25. Observe the Actions log and check the ManaPool dashboard afterward. Download the `manapool-live-<run id>` artifact containing the full preview report and `repricing_updates.csv` journal.

### Enable nightly at 8 PM Eastern

After verifying the first full run, GitHub repository → Settings → Secrets and variables → Actions → **Variables** → New repository variable: `MANAPOOL_ENABLE_NIGHTLY` = `true` (exact lowercase). The scheduled cron fires at 00:00 and 01:00 UTC; Python's New York-hour guard skips the wrong one. GitHub-hosted cron can be delayed and isn't guaranteed to execute exactly at 8 PM.

Set the variable to `false` (or delete it) to disable future scheduled price writes. Manual runs require confirmation even when the variable is enabled.

## Offline/online safety

The script checks `GET /account`, records original `singles_live`, sets it to `false`, and confirms it before making price writes. `sealed_live` and shipping are untouched. On normal success/errors it restores the **original** state (if it was already offline, it stays offline). It logs and fails if restoration cannot be confirmed.

The internal deadline is 125 minutes, ahead of the GitHub Actions 150-minute job timeout. This is a **best-effort** safeguard. A runner crash, forced cancellation, loss of network, GitHub outage, or process kill can prevent the `finally` restoration. No workflow can guarantee restoration after the runner disappears.

**Emergency recovery:** GitHub Actions → **ManaPool Emergency - Restore Singles Live** → Run workflow → confirmation `RESTORE_SINGLES_LIVE`. This **unconditionally sets singles live**; use it only when you want your store online. Unlike the main workflow, it has no concurrency lock, so do NOT run it while the repricer is actively changing prices. If GitHub Actions is unavailable, use the ManaPool seller dashboard to restore singles visibility manually. Verify `singles_live=true` afterward.

## Operational precautions

- The first full run may take several minutes or longer; the store is offline while it runs.
- A journal is written and flushed after each bulk batch. `VERIFIED_RESPONSE` confirms price and quantity in the bulk POST response, not a later independent GET.
- Do not manually change listings while the repricer runs.
- Because prices are derived from a snapshot, market changes during a run may make targets stale. Each batch checks inventory freshness, but does not fetch the catalog low again per card.
- If an update fails, the script stops and restores original visibility. Earlier successful changes remain in place; rerunning recalculates from current inventory.
- Review `repricing_updates.csv` for `SKIPPED_STALE`, `UNKNOWN_NETWORK`, `HTTP_ERROR`, or `VERIFY_FAILED` statuses.
- GitHub Actions workflow concurrency prevents overlapping price-update runs. The emergency recovery workflow intentionally bypasses this lock.


## Bulk update

Uses `POST /seller/inventory/product` with batches of at most 1000. Each entry includes `quantity: null` to preserve inventory quantities. Every returned product is verified against the intended price and original quantity; skipped or missing results stop the run. Network failures are not automatically retried because the outcome may be unknown. Earlier successful batches remain applied. Run manually and verify before enabling nightly scheduling.


## Repository layout

```text
manapool_repricer/
├── .github/workflows/
│   ├── scheduled-repricing.yml
│   └── emergency-restore.yml
├── debug/
│   ├── README.md
│   ├── test_bulk_update.py
│   └── test_repricing_safety.py
├── .gitignore
├── README.md
├── account_visibility.py
├── full_repricing.py
├── inventory.py                 # Keep your existing file; not included
└── repricing_preview.py
```

All debugging and test files are in `debug/`; production scripts remain in the root so GitHub Actions works without workflow changes. Local logs and CSV outputs are ignored by Git. GitHub Actions still uploads CSV reports as workflow artifacts.

Run tests from the repository root: `python -m unittest discover -s debug -p 'test_*.py'`.

**Cleanup of previously committed files:** after extracting and committing, remove the old root-level `test_bulk_update.py` and `test_repricing_safety.py` if Git still tracks them (`git rm test_bulk_update.py test_repricing_safety.py`). Do not delete your existing `inventory.py`.

## Repricing exceptions
Place one or more CSV files directly in `card_exception/` and commit them before running the workflow. All CSV files (including uppercase .CSV) are loaded in filename order; other files and nested directories are ignored. Duplicate identities are merged, with distinct reasons combined. Keep at least one header-only CSV if there are no exceptions. Move any old root-level `repricing_exceptions.csv` into this directory; the root file is no longer read. Required fields: `scryfall_id` (UUID), `card_name`, `set_code`, `is_foil` (`true` or `false`). `reason` is optional. All four fields match case-insensitively after trimming; condition and language are ignored. FO and EF both count as foil; NF is non-foil. Exceptions override the price floor. Missing directory, no CSV files, or any malformed CSV stops live execution before visibility changes. When exceptions exist, missing/conflicting API identity is held for review rather than repriced. Same ID/foil with conflicting name or set is also held for review. The report includes identity and exception fields. The CSV is public if your repository is public. Do not include private notes.

## Configurable minimum price
In GitHub Settings → Secrets and variables → Actions → Variables, set `MANAPOOL_MIN_PRICE_CENTS` to a positive integer in cents (e.g. `75`). Missing variable defaults to 75; invalid values stop execution before API writes or visibility changes. Local runs can set the same environment variable. Exceptions override the minimum. Changes apply on the next workflow run.
