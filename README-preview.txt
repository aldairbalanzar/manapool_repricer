Copy repricing_preview.py to your repository root (beside inventory.py).
Copy .github/workflows/repricing-preview.yml to .github/workflows/.

Push both files, then manually run "ManaPool Repricing Preview (Read Only)" from GitHub Actions.
Download the repricing-report artifact to review the CSV.

The code makes GET requests only and NEVER updates listings.
No automated repricing, approval, email or scheduled workflow is included.

IMPORTANT: The GET /api/v1/products/singles endpoint path is inferred from the supplied schema;
if the endpoint responds with 404 or another error, verify the exact URL in ManaPool's docs.
The catalog may include your own listing in low_price, so all rows remain unapproved.
