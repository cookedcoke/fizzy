"""The customer display's idle screen: QR codes for My CCC on the App Store and the student site."""
from fizzy import config
from tests.test_app import client, login  # noqa: F401  (fixture)


def test_idle_screen_shows_the_app_store_code(client, monkeypatch):
    login(client)
    monkeypatch.setattr(config, "PUBLIC_HOST", "my.example.com")
    page = client.get("/display").text
    assert "Get the app" in page and "my.example.com" in page and page.count("<figure>") == 2


def test_blank_app_store_link_leaves_it_off(client, monkeypatch):
    login(client)
    monkeypatch.setattr(config, "STUDENT_APP_STORE_URL", "")
    page = client.get("/display").text
    assert "Get the app" not in page
