# Debug and tests

This directory contains developer tests only. The scheduled and emergency GitHub Actions workflows do not execute files from here.

From the repository root, run:

```bash
python -m unittest discover -s debug -p 'test_*.py'
```

Tests require the existing root-level `inventory.py` and the `requests` package. Do not place ManaPool tokens or API response logs in this directory.
