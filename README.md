# ManaPool nightly live repricer

This package replaces the old **read-only scheduled workflow** with an **opt-in** live workflow. It retains the existing `inventory.py` from your repository (DO NOT remove or replace it). It uses `requests` and Python 3.12.

## Pricing rules

- Only active `mtg_single` listings with positive quantity.
- Raise listings below $0.75 to $0.75.
- If above the reported market low, decrease to $0.01 **above** market low, never below $0.75.
- If already at or below market low, leave unchanged (except floor).
- The reported low can include your own listing. This is **not** a competitor-only price.
- Write ONLY `{ "price_cents": target, "quantity": null }`. Never modify quantities.
- One listing per API request, up to 10,000 eligible changes per run. Stop on API errors or verification mismatches. Skip stale listings detected during periodic inventory refreshes.

## Install

Copy `full_repricing.py`, `account_visibility.py`, `repricing_preview.py`, `recover_visibility.py` and `.github/workflows/` into the root of your existing `manapool_repricer` repo. Keep your `inventory.py` and existing GitHub secrets `MANAPOOL_EMAIL`, `MANAPOOL_TOKEN`. The token must have inventory-write and seller-settings-write permissions. **Delete/disable the older pilot-25 workflow** to avoid accidentally running it.

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

- The first full run could take over an hour; the store is offline while it runs.
- A journal is written and flushed after each update. `VERIFIED_RESPONSE` confirms price and quantity in the PUT response, not a later independent GET.
- Do not manually change listings while the repricer runs.
- Because prices are derived from a snapshot, market changes during a run may make targets stale. Each update checks inventory freshness, but does not fetch the catalog low again per card.
- If an update fails, the script stops and restores original visibility. Earlier successful changes remain in place; rerunning recalculates from current inventory.
- Review `repricing_updates.csv` for `SKIPPED_STALE`, `UNKNOWN_NETWORK`, `HTTP_ERROR`, or `VERIFY_FAILED` statuses.
- GitHub Actions workflow concurrency prevents overlapping price-update runs. The emergency recovery workflow intentionally bypasses this lock.
