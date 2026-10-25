from tests.test_app import client, login  # noqa: F401  (fixture)


def test_installable(client):
    m = client.get("/manifest.webmanifest").json()
    assert m["start_url"] == "/" and m["display"] == "standalone" and any(i.get("purpose") == "maskable" for i in m["icons"])
    for icon in m["icons"]:
        assert client.get(icon["src"]).status_code == 200
    assert client.get("/me/manifest.webmanifest").json()["start_url"] == "/me"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and "offline.html" in sw.text
    assert "You're offline" in client.get("/static/offline.html").text
    login(client)
    page = client.get("/").text
    assert 'rel="manifest"' in page and 'serviceWorker' in page
    assert 'href="/me/manifest.webmanifest"' in client.get("/me").text
