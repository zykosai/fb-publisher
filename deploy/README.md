# Advanced Linux/server setup

Windows is the primary desktop workflow. Linux needs a graphical session for manual
Facebook login, even though background publication can run headless. Use a private
desktop/VNC session or a display server you control; do not expose VNC publicly.

Create an unprivileged service user, place the project at `/opt/fb-publisher`, and
give that user ownership of the project/runtime directory. Run `setup_ubuntu.sh`
as that user after installing Python 3.11+ and its venv/system browser dependencies.
The script does not install system packages or configure a firewall for you.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/web.py
```

Edit `fb-publisher.service` to match your user, paths and graphical display before
installing it. Only the panel service is needed: account workers are launched by
the dashboard. Do not enable an old global worker service alongside account workers.
The template binds the panel to localhost. Use a private tunnel, or deliberately
configure `--lan` plus a private-network-only firewall rule. Never forward the
HTTP dashboard to the public internet.

The host must remain on. Linux deployment and remote graphical login need their
own testing; passing desktop tests is not proof of a production server setup.
