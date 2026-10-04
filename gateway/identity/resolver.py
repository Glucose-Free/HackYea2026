from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

OPEN_WEBUI_USER_ID_HEADER = "X-OpenWebUI-User-Id"
OPEN_WEBUI_USER_EMAIL_HEADER = "X-OpenWebUI-User-Email"
OPEN_WEBUI_USER_NAME_HEADER = "X-OpenWebUI-User-Name"


@dataclass(frozen=True)
class UserIdentity:
    user_id: str
    email: str
    name: str


class IdentityResolver(Protocol):
    def resolve(self, headers: Mapping[str, str]) -> UserIdentity | None: ...


class OpenWebUiHeaderResolver:
    """Trusts Open WebUI's forwarded user headers; safe only because the chat API also requires the shared key."""

    def resolve(self, headers: Mapping[str, str]) -> UserIdentity | None:
        lowercase_headers = {name.lower(): value for name, value in headers.items()}
        user_id = lowercase_headers.get(OPEN_WEBUI_USER_ID_HEADER.lower(), "").strip()
        if not user_id:
            return None
        return UserIdentity(
            user_id=user_id,
            email=lowercase_headers.get(OPEN_WEBUI_USER_EMAIL_HEADER.lower(), "").strip(),
            name=lowercase_headers.get(OPEN_WEBUI_USER_NAME_HEADER.lower(), "").strip(),
        )
