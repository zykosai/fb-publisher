from types import SimpleNamespace

from bike_publisher.config import Settings
from bike_publisher.facebook import FacebookPublisher


def test_background_default_and_explicit_visible_login(tmp_path, monkeypatch):
    import playwright.sync_api

    monkeypatch.delenv('BIKEBOT_HEADLESS', raising=False)
    monkeypatch.setenv('DISPLAY', ':99')
    settings = Settings.from_root(tmp_path)
    assert settings.headless is True
    calls = []
    context = object()

    def launch(**kwargs):
        calls.append(kwargs)
        return context

    runtime = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', lambda: SimpleNamespace(start=lambda: runtime))
    publisher = FacebookPublisher(settings)
    assert publisher.launch()[1] is context
    assert calls[-1]['headless'] is True
    assert calls[-1]['viewport'] == {'width': 1440, 'height': 1000}
    publisher.launch(headless=False)
    assert calls[-1]['headless'] is False
    assert '--start-maximized' not in calls[-1]['args']
    assert '--window-size=1100,850' in calls[-1]['args']
    assert calls[-1]['viewport'] == {'width': 1080, 'height': 720}


def test_background_mode_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('BIKEBOT_HEADLESS', 'false')
    assert Settings.from_root(tmp_path).headless is False
