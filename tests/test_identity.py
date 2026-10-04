from gateway.identity.resolver import OpenWebUiHeaderResolver, UserIdentity


def test_resolves_identity_from_open_webui_headers_case_insensitively():
    headers = {"x-openwebui-user-id": "u-1", "x-openwebui-user-email": "alice@demo.local", "x-openwebui-user-name": "Alice"}
    assert OpenWebUiHeaderResolver().resolve(headers) == UserIdentity("u-1", "alice@demo.local", "Alice")


def test_missing_or_blank_user_id_resolves_to_none():
    assert OpenWebUiHeaderResolver().resolve({}) is None
    assert OpenWebUiHeaderResolver().resolve({"X-OpenWebUI-User-Id": "  "}) is None


def test_email_and_name_default_to_empty():
    assert OpenWebUiHeaderResolver().resolve({"X-OpenWebUI-User-Id": "u"}) == UserIdentity("u", "", "")


def test_percent_encoded_name_from_open_webui_is_decoded():
    headers = {"X-OpenWebUI-User-Id": "u", "X-OpenWebUI-User-Name": "Micha%C5%82 Rygorowicz"}
    assert OpenWebUiHeaderResolver().resolve(headers).name == "Michał Rygorowicz"
