def test_healthcheck(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
