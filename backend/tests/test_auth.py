from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import RefreshToken
from app.services import email as email_service
from app.services.auth import ACCESS_COOKIE, REFRESH_COOKIE

SIGNUP = {
    "company_name": "Rwanda Coffee Exporters Ltd",
    "full_name": "Aline Uwase",
    "email": "aline@coffee.example",
    "password": "correct horse battery staple",
}
PASSWORD = SIGNUP["password"]


def test_signup_sets_httponly_cookies_and_never_returns_tokens(client: TestClient) -> None:
    response = client.post("/api/v1/auth/signup", json=SIGNUP)

    assert response.status_code == 201
    body = response.json()
    assert body["company_id"] is not None
    assert "access_token" not in body and "refresh_token" not in body

    cookies = {cookie.name: cookie for cookie in client.cookies.jar}
    assert set(cookies) == {ACCESS_COOKIE, REFRESH_COOKIE}
    for cookie in cookies.values():
        assert cookie.has_nonstandard_attr("HttpOnly")


def test_login_rejects_a_wrong_password(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=SIGNUP)
    client.cookies.clear()

    response = client.post(
        "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "invalid_credentials",
        "message": "Invalid email or password",
        "field_errors": {},
    }


def test_login_auto_selects_the_only_membership(client: TestClient) -> None:
    signup = client.post("/api/v1/auth/signup", json=SIGNUP).json()
    client.cookies.clear()

    response = client.post(
        "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["company_id"] == signup["company_id"]


def test_me_reports_the_owner_permission_set(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=SIGNUP)

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == SIGNUP["email"]
    assert body["memberships"][0]["is_owner"] is True
    assert "gl:journal_post" in body["permissions"]
    assert body["is_email_verified"] is False


def test_me_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


def test_refresh_rotates_the_token_and_the_old_one_dies(
    client: TestClient, db: Session
) -> None:
    client.post("/api/v1/auth/signup", json=SIGNUP)
    original_refresh = client.cookies[REFRESH_COOKIE]

    rotated = client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    assert client.cookies[REFRESH_COOKIE] != original_refresh

    replay_client = TestClient(client.app)
    replay_client.cookies.set(REFRESH_COOKIE, original_refresh)
    replay = replay_client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401
    assert replay.json()["code"] == "token_reused"

    live = db.scalars(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))).all()
    assert live == []


def test_logout_clears_cookies_and_revokes_the_refresh_token(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=SIGNUP)

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.cookies.get(ACCESS_COOKIE) is None
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_password_reset_round_trip(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=SIGNUP)
    client.cookies.clear()
    email_service.outbox.clear()

    requested = client.post(
        "/api/v1/auth/password-reset/request", json={"email": SIGNUP["email"]}
    )
    assert requested.status_code == 202
    token = email_service.outbox[-1].context["token"]

    confirmed = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "a whole new passphrase"},
    )
    assert confirmed.status_code == 204

    assert (
        client.post(
            "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": SIGNUP["email"], "password": "a whole new passphrase"},
        ).status_code
        == 200
    )


def test_password_reset_never_reveals_unknown_accounts(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/password-reset/request", json={"email": "nobody@unknown.example"}
    )
    assert response.status_code == 202
    assert email_service.outbox == []


def test_password_reset_token_is_single_use(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=SIGNUP)
    client.post("/api/v1/auth/password-reset/request", json={"email": SIGNUP["email"]})
    token = email_service.outbox[-1].context["token"]
    payload = {"token": token, "new_password": "a whole new passphrase"}

    assert client.post("/api/v1/auth/password-reset/confirm", json=payload).status_code == 204
    assert client.post("/api/v1/auth/password-reset/confirm", json=payload).status_code == 401


def test_email_verification_round_trip(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=SIGNUP)
    token = email_service.outbox[-1].context["token"]

    assert (
        client.post("/api/v1/auth/email-verification/confirm", json={"token": token}).status_code
        == 204
    )
    assert client.get("/api/v1/auth/me").json()["is_email_verified"] is True


def test_signup_rejects_a_duplicate_email(client: TestClient) -> None:
    client.post("/api/v1/auth/signup", json=SIGNUP)

    response = client.post("/api/v1/auth/signup", json={**SIGNUP, "company_name": "Another Ltd"})

    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


def test_signup_enforces_a_password_policy(client: TestClient) -> None:
    response = client.post("/api/v1/auth/signup", json={**SIGNUP, "password": "short"})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "password" in response.json()["field_errors"]
