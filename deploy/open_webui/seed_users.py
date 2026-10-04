"""Creates the demo accounts in Open WebUI so the jury can log in without setup. Safe to re-run."""

import os
import sys
import time

import httpx

OPEN_WEBUI_URL_ENV = "OPEN_WEBUI_URL"
ADMIN_EMAIL_ENV = "WEBUI_ADMIN_EMAIL"
ADMIN_PASSWORD_ENV = "WEBUI_ADMIN_PASSWORD"
DEMO_USER_PASSWORD_ENV = "DEMO_USER_PASSWORD"
DEFAULT_OPEN_WEBUI_URL = "http://open-webui:8080"
SIGNIN_PATH = "/api/v1/auths/signin"
ADD_USER_PATH = "/api/v1/auths/add"
HEALTH_PATH = "/health"
DEMO_USER_ROLE = "user"
STARTUP_ATTEMPTS = 60
STARTUP_RETRY_SECONDS = 2
DEMO_USERS = [
    ("Alice Analyst", "alice@demo.local"),
    ("Bob Banker", "bob@demo.local"),
]


def wait_for_open_webui(client: httpx.Client) -> None:
    for _ in range(STARTUP_ATTEMPTS):
        try:
            if client.get(HEALTH_PATH).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(STARTUP_RETRY_SECONDS)
    sys.exit("Open WebUI did not become healthy")


def get_admin_token(client: httpx.Client) -> str:
    response = client.post(SIGNIN_PATH, json={"email": os.environ[ADMIN_EMAIL_ENV], "password": os.environ[ADMIN_PASSWORD_ENV]})
    response.raise_for_status()
    return response.json()["token"]


def add_demo_user(client: httpx.Client, admin_token: str, name: str, email: str, password: str) -> None:
    response = client.post(
        ADD_USER_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "email": email, "password": password, "role": DEMO_USER_ROLE},
    )
    # 400 means the account already exists, which is fine on a re-run.
    if response.status_code not in (200, 400):
        response.raise_for_status()
    print(f"{email}: {response.status_code}")


def main() -> None:
    with httpx.Client(base_url=os.environ.get(OPEN_WEBUI_URL_ENV, DEFAULT_OPEN_WEBUI_URL), timeout=10) as client:
        wait_for_open_webui(client)
        admin_token = get_admin_token(client)
        for name, email in DEMO_USERS:
            add_demo_user(client, admin_token, name, email, os.environ[DEMO_USER_PASSWORD_ENV])


if __name__ == "__main__":
    main()
