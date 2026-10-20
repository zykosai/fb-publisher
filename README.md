# FB Publisher

Local-first Facebook group publishing assistant for products, services and content.

[Русская инструкция](README.ru.md) · [Detailed guide (RU)](WEB-APP.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## What it does

- Separate saved Facebook accounts with independent browser sessions, catalogs, groups, queues and pause controls.
- Concurrent workers for different accounts; no need to log out to switch the dashboard.
- Product, Services and Content / AI interface presets. The AI preset is not an AI generator.
- Manual listings, ZIP import and preview-first import of your own Marketplace listings.
- Ordered photos, optional headings, reversible SOLD status and sales totals.
- Group checks, native Facebook group batches where available, scheduling and delivery history.
- Eleven interface languages, including Arabic right-to-left layout.
- A responsive local web dashboard for desktop and phone; optional PWA installation where the browser permits it.

No paid API or cloud subscription is required. Your own computer or server runs the application.

## Important limitations

This is an independent, unofficial project, not affiliated with Meta or Facebook.
It automates the browser UI rather than an official Facebook publishing API.
Facebook UI changes may break selectors. Some groups require approval, membership
questions or manual actions. Multiple accounts and Turbo do not prevent restrictions.
The application must stop when Facebook restricts an account; do not use it to bypass limits,
spam groups or publish content without permission.

## Quick start — Windows

1. Install **Python 3.11 or newer**. A working internet connection is needed for the first installation.
2. Download the source ZIP from this repository or a release and **extract it completely**.
3. Double-click **`PC START.cmd`**.
4. The first launch creates `.venv`, installs dependencies and downloads a fallback Chromium browser.
   Google Chrome is used when installed.
5. Keep the launch window open. Use the **full connection link** it prints, including `?key=...`.
6. Open **Accounts → Add account → Sign in to selected account**. Enter credentials only on Facebook,
   then confirm login in the dashboard.
7. Create or import listings, load and check your groups, create a publishing plan and start that account.

Do not launch the application from inside the ZIP. Administrator privileges are not required for normal local use.
If Windows blocks a downloaded launcher, inspect the files before allowing them to run.
The launchers apply PowerShell's execution-policy bypass only to their own process, not system-wide.

| File | Purpose |
| --- | --- |
| `PC START.cmd` | Start the local dashboard |
| `TAILSCALE START.cmd` | Start LAN / private-network access |
| `STOPBOT.cmd` | Pause all accounts, stop their workers and release the dashboard port |
| `SETUP.cmd` | Install or repair dependencies without starting the dashboard |

The default local address is `http://localhost:8765`. The connection key is private:
do not put its link in screenshots, issues or public posts.

## First-use workflow

1. **Accounts:** create a separate profile for each Facebook login.
2. **Catalog:** add descriptions and photos; drag photos into the required order.
3. **Groups:** load memberships and check posting availability. Checks can be cancelled.
4. **Publish:** select listings and groups, choose the interval, create a plan and start the account.
5. **History:** review sent, pending-moderation and uncertain results. Do not blindly repeat uncertain submissions.

Only a manually entered heading is used. A blank heading means no heading.
Deleting a catalog item does not delete its existing Facebook posts.

## Phone and remote access

The bot still runs on the host computer, which must stay on and awake.
Use `TAILSCALE START.cmd` for LAN/private VPN access and open the printed connection link
with the host's reachable address. The phone and host must have network access to each other.
Firewall changes may be needed. **Do not forward port 8765 to the public internet.**
PWA installation depends on the browser and secure-context requirements; plain HTTP LAN access
is not a guarantee that an install button will be available.

## Configuration and private data

Defaults work without a configuration file. For overrides, copy `.env.example` to `.env`.
See [the configuration example](.env.example). Never publish `.env`.

All runtime state normally lives in **`data/`**: SQLite, login cookies, account browser profiles,
photos, connection keys, logs and backups. The whole directory is excluded from Git and release archives.
Back it up privately before updating; keep your own `data/` and `.env` when replacing source files.
Do not share an existing Chrome profile or logged-in release ZIP with another user.
Every new installation must use its own Facebook logins.

## Development

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Dev
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\check_distribution.py
.\.venv\Scripts\python.exe scripts\build_release.py
```

Tests use temporary databases and mocked/local browser pages. They must not publish to Facebook.
See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture, translation and test guidance.
Linux/server installation is advanced; see [deploy/README.md](deploy/README.md).
The legacy Streamlit entry point remains for compatibility; the supported user dashboard is `scripts/web.py`.

## Troubleshooting

- **Port 8765 is busy:** another dashboard may be running. Use `STOPBOT.cmd`; it refuses to kill an unrelated application.
- **Dashboard asks for a connection link:** open the complete link printed by the current launcher.
- **Login browser closes or Facebook asks for verification:** reopen login and complete the challenge manually.
- **Group result is uncertain:** inspect the result before retrying, to avoid duplicate posts.
- **Phone cannot connect:** first check local host access, then VPN/LAN reachability and firewall rules.
- **Installation fails:** run `SETUP.cmd` and inspect the error. Do not upload private logs without redaction.

## Support the project

If FB Publisher helps you, you can support its continued development with a voluntary donation.

**TRON / TRC20 wallet:**

```text
TAEVBKga2Lq4hm8e7eFrPc39CVQHRxfsyx
```

Please verify the address and network before sending. Donations are optional; the project remains free and open source. Bug reports, translations and code contributions are also welcome.

## License

[MIT](LICENSE). Dependencies keep their own licenses.
The license covers this software, not Facebook, third-party trademarks or user-uploaded content.
