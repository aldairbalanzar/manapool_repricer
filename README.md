# ManaPool Repricer

A Python and GitHub Actions project for reviewing and safely repricing existing **Magic: The Gathering** listings on ManaPool.

> **Status: Early development.** The only implemented feature in this starter project is a manually triggered, **read-only inventory connectivity test**. The pricing engine, report, email delivery, approval flow, and price updates described below are planned, **not yet implemented**. ManaPool market-price and price-write capabilities still need verification.

## Project goals

- Analyze the seller's existing ManaPool inventory once per day.
- Recommend competitive listing prices without going below a configured floor.
- Generate **one** `repricing_report.csv` containing all listings, proposed changes, and review flags.
- Email that CSV to the configured recipient after the daily report is generated successfully.
- Require explicit approval before changing any prices.
- **Never intentionally create listings, delete listings, or change inventory quantities.**
- Stop on unexpected mismatches and provide clear, color-coded GitHub Actions logs.

## Planned daily workflow

1. GitHub Actions starts at **8:00 PM America/New_York** (Eastern Time), once per day. Scheduled runs may start late.
2. The Python script reads the full ManaPool inventory, using pagination.
3. It retrieves comparable market-low prices, matching the exact card and relevant printing/condition attributes (subject to available ManaPool data).
4. It calculates proposed prices and flags potentially undervalued listings.
5. It creates a single `repricing_report.csv` and emails it to the configured address.
6. The daily job **does not update inventory**. Price changes require a separate, manually initiated approval/apply workflow.
7. Before and after approved changes, the apply workflow validates listing identities, prices, and quantities; it stops on unexpected differences and performs read-only reconciliation after uncertain outcomes.

## Planned pricing rules

| Setting | Planned value |
| --- | --- |
| Minimum listing price | `$0.75` |
| Undercut amount | `$0.01` |
| Target price | `max($0.75, market_low - $0.01)` |
| Below-market flag | Current price is at least 20% below market low **and** market low is greater than `$5.00` |
| Automatic price updates | Disabled |
| Approval required | Yes |
| Initial update batch size | 25 listings |

Prices will be calculated with decimal-safe arithmetic. Comparable listings must be matched accurately, and the repricer should avoid unnecessary price reductions when a seller is already the lowest-priced listing. The market-low data source and write/update mechanism must be verified before live repricing is enabled.

## Planned report

A single CSV, `repricing_report.csv`, will contain all inventory listings, including unchanged listings. Planned snake_case fields include:

`sku`, `card_name`, `set_code`, `condition`, `foil_status`, `quantity`, `current_price`, `market_low`, `target_price`, `price_difference`, `below_market_pct`, `is_below_market`, `approved`, `verification_status`.

The report will also contain enough run/snapshot metadata to validate an approval against fresh inventory. Boolean values will use `true` and `false`, not `YES` or `NO`. An empty `approved` field means no approval was granted.

## Planned safety controls

- Use a **read-only ManaPool token** for inventory retrieval and preview/report generation.
- Keep any future write-enabled credentials separate and limited to the manual apply workflow.
- Compare inventory identities and quantities before submitting updates.
- Prefer verified **price-only** updates; do not use an import path until its duplicate-listing and quantity semantics are confirmed safe.
- Submit updates sequentially in batches of up to 25, validating each batch before proceeding.
- Halt on mismatches; don't blindly retry uncertain writes or automatically alter quantities to reconcile them.
- Prevent concurrent approval runs.
- Use logs with normal-color INFO, green SUCCESS, bright yellow WARNING, red ERROR, and purple CRITICAL messages.

Real sales may change quantities while the workflow is running; reconciliation must distinguish legitimate sales from unintended repricer changes where possible.

## Repository structure (current)

```text
manapool_repricer/
├── .github/
│   └── workflows/
│       └── test-inventory.yml  # Manual read-only connectivity test
├── inventory.py                # Fetches all inventory pages and prints counts
├── requirements.txt            # Python dependencies
└── README.md
```

## Getting started: current read-only test

1. Create a **private** GitHub repository and add these files.
2. In **Settings → Secrets and variables → Actions**, configure repository secrets:
   - `MANAPOOL_EMAIL`: ManaPool account email.
   - `MANAPOOL_TOKEN`: ManaPool API token with read-only inventory access.
3. Open **Actions → Test ManaPool Inventory (Read Only) → Run workflow**.
4. Inspect the logs. A successful run prints the number of pages and listings retrieved, without printing individual cards or modifying inventory.

The current workflow is **manual only**; no daily schedule or email delivery is enabled yet.

### Run locally (optional)

Requires Python 3.12+.

```bash
python -m pip install -r requirements.txt
export MANAPOOL_EMAIL='your-account-email'
export MANAPOOL_TOKEN='your-read-only-token'
python inventory.py
```

Do not commit credentials, `.env` files containing secrets, or exported inventory reports. Use GitHub Actions Secrets for workflow credentials. Email delivery will require its own authenticated sending method; connecting Gmail to ChatGPT does not automatically authorize GitHub Actions to send mail.

## Implementation roadmap

- [x] Create read-only inventory test script and manual GitHub Actions workflow (starter files prepared; live run not yet verified).
- [ ] Verify ManaPool inventory retrieval against the account and confirm response/pagination behavior.
- [ ] Verify access to comparable market-low pricing.
- [ ] Implement pricing calculations, matching rules, and unit tests.
- [ ] Generate the single CSV report.
- [ ] Add once-daily **8 PM Eastern** GitHub Actions schedule.
- [ ] Configure authenticated email delivery with CSV attachment and failure handling.
- [ ] Verify a safe price-only update path with ManaPool.
- [ ] Implement explicit approval, preflight checks, sequential batches, post-update verification, and reconciliation.
- [ ] Test with mock data, a live read-only run, and a controlled approved update before broader rollout.

## Non-goals

This project is **not** an inventory import/synchronization tool. It must not add cards, remove cards, change quantities, or automatically approve repricing changes.
