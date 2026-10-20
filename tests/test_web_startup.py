import errno
from unittest.mock import MagicMock

import pytest

from bike_publisher import webapp


def test_browser_opens_pairing_link_and_interrupt_closes_cleanly(monkeypatch):
    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt
    app = MagicMock()
    app.key = "test-key"
    opener = MagicMock(return_value=True)
    monkeypatch.setattr("sys.argv", ["web.py", "--open-browser"])
    monkeypatch.setattr(webapp, "ThreadingHTTPServer", MagicMock(return_value=server))
    monkeypatch.setattr(webapp, "Application", MagicMock(return_value=app))
    monkeypatch.setattr(webapp.webbrowser, "open", opener)
    webapp.main()
    opener.assert_called_once_with("http://localhost:8765/?key=test-key")
    app.db.set_setting.assert_called_once_with("publication_paused", True)
    server.server_close.assert_called_once()


def test_duplicate_server_does_not_touch_database(monkeypatch, capsys):
    application = MagicMock()
    monkeypatch.setattr("sys.argv", ["web.py"])
    monkeypatch.setattr(webapp, "Application", application)
    monkeypatch.setattr(webapp, "ThreadingHTTPServer", MagicMock(side_effect=OSError(errno.EADDRINUSE, "Address in use")))
    with pytest.raises(SystemExit) as exc:
        webapp.main()
    assert exc.value.code == 1
    assert "already in use" in capsys.readouterr().err
    application.assert_not_called()


def test_browser_failure_does_not_stop_server(monkeypatch, capsys):
    server = MagicMock()
    monkeypatch.setattr("sys.argv", ["web.py", "--open-browser"])
    monkeypatch.setattr(webapp, "ThreadingHTTPServer", MagicMock(return_value=server))
    monkeypatch.setattr(webapp, "Application", MagicMock(return_value=MagicMock(key="test-key")))
    monkeypatch.setattr(webapp.webbrowser, "open", MagicMock(side_effect=OSError("No browser")))
    webapp.main()
    server.serve_forever.assert_called_once()
    assert "Copy the URL" in capsys.readouterr().out
