# Contributing to FB Publisher

Bug reports, translations and focused pull requests are welcome.

## Local environment

Use Python 3.11+ and a dedicated virtual environment. On Windows run `setup.ps1 -Dev`.
On other systems create `.venv`, install `requirements-dev.txt`, then install the
Playwright browsers. Browser tests currently launch the Chrome channel:

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install --with-deps chrome
PYTHONPATH=src python -m pytest -q
```

Do not run installation commands against another person's active browser session.
The CI environment is disposable; it does not contain Facebook credentials.

## Structure

- `src/bike_publisher/`: database, account isolation, planning, workers and Facebook adapter.
- `web/`: responsive dashboard, static translations and service worker.
- `scripts/web.py`, `scripts/worker.py`: entry points.
- `tests/`: temporary SQLite fixtures and local browser regressions.
- `src/bike_publisher/distribution.py`: explicit public-file manifest and release checks.

The internal Python package remains `bike_publisher` for compatibility. Branding
changes must not rename or move existing users' `data/` directories.

## Safety rules

- Never commit `data/`, cookies, tokens, `.env`, real product photos or account snapshots.
- Tests and CI must not perform real joins, posts, deletions or account changes on Facebook.
- Do not fabricate membership answers or bypass challenges, restrictions or moderation.
- Preserve per-account isolation and duplicate/uncertain-submission protections.
- Do not add runtime translation calls that transmit product or account content.
- A clean startup must never publish automatically before the user creates and starts a plan.

## Pull requests

Keep changes focused, explain the behavior and add a regression test. Run the full
test suite and `python scripts/check_distribution.py` before opening a PR. Redact
screenshots and logs; use fictional profile/group names and `example.test` addresses.
If changing a launcher, check Windows PowerShell 5.1 parsing and keep launcher text
ASCII or explicitly handle encoding. Do not change execution policy system-wide.

## Translations

Russian/English/Polish UI strings live in `web/i18n.js`; the other static catalogs
are in `web/locales.js`. Keep matching source keys and placeholders such as `{n}`.
Never translate user-entered product names, descriptions or group names. Verify
mobile layout, Arabic RTL and the catalog-completeness test after changes.

## Releases

`python scripts/build_release.py` builds a source archive from an allowlist and
adds an integrity manifest. It never copies a developer's runtime `data/` folder.
Review the manifest and run tests from an extracted copy before publishing it.
