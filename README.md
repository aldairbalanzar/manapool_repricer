# ManaPool 25-listing live pilot

This folder contains complete replacements/additions for the existing repository. Keep your **existing `inventory.py` unchanged**.

## Files

- `repricing_preview.py` — current $0.01-above-low, $0.75-floor read-only report
- `scheduled_repricing.py` — existing scheduled preview validation
- `.github/workflows/scheduled-repricing.yml` — scheduled **read-only** preview
- `pilot_25.py` — manual live pilot, maximum 25 price-only updates
- `.github/workflows/pilot-25.yml` — manually triggered live pilot

## Before running

1. Review that `MANAPOOL_EMAIL` and `MANAPOOL_TOKEN` are configured as repository secrets. **The token must have inventory:write access.** Never paste it into chat or commit it.
2. Replace/add the files in the repository (including the hidden `.github` directory).
3. Commit and push to `main`.
4. Go to **Actions → ManaPool Live Pilot - 25 Price Updates → Run workflow**.
5. Enter `CONFIRM_25_PRICE_UPDATES` and run. This is a real write operation, not a preview.
6. Download the `manapool-pilot-...` artifact and inspect `pilot_25_results.csv`.
7. Verify prices and quantities in ManaPool before any larger rollout.

## Safety

- Hard cap **25** per invocation; no loop or automatic scheduling of this pilot.
- A fresh report is generated, and seller inventory is fetched again before writes.
- Aborts without writing if selected listings' prices or quantities changed before preflight.
- Writes `{"price_cents": <target>, "quantity": null}` only, per documented endpoint.
- Checks returned product ID, price and quantity after each update; stops on first failure.
- A network timeout leaves outcome unknown: inspect ManaPool and the journal before retrying.
- `pilot_25_results.csv` is flushed after every attempted update, but GitHub artifact upload happens after the job.
- The 8 PM Eastern workflow **remains preview-only**. This pilot does not turn on unattended writes.

## Pricing rules

- Price floor is 75 cents.
- If price is already at/below market low, do not lower it unless below floor.
- If price is above market low, target max(75, market low + 1) cents.
- A reported market low can include your own listing; the pilot cannot isolate competitor prices.

**Note:** The pilot uses the first 25 eligible products in sorted product-ID order, which may all be floor increases. No particular card selection is promised. The API response verifies the update but is not an independent post-write GET check.

## Offline / online safety (new)

The manually dispatched **ManaPool Offline Repricing Pilot - 25 Updates** workflow
reads `GET /account` before updating. If singles were live, it sets
`PUT /account` with `{"singles_live": false}`, verifies via GET, updates up to
25 prices, then restores and verifies `singles_live: true` in a `finally` block.
If singles were already offline, they remain offline. Sealed listings and
shipping settings are not intentionally modified. A failed pause blocks all
price updates. If restoration fails (including network outages), the job fails
and logs a CRITICAL message: check the ManaPool dashboard immediately.
A forcibly canceled GitHub job or runner failure cannot execute Python cleanup;
check singles visibility after any interrupted run. Keep the scheduled workflow
in preview-only mode until this end-to-end pilot is verified.

**Before running:** confirm the API token supports seller account settings write
and inventory write. Use the workflow confirmation `CONFIRM_25_PRICE_UPDATES`.
