import secrets

from fastapi import Depends, HTTPException, Request, status

from gateway.components import GatewayComponents

BEARER_PREFIX = "Bearer "
INVALID_API_KEY_DETAIL = "missing or invalid API key"


def get_components(request: Request) -> GatewayComponents:
    return request.app.state.components


def require_gateway_api_key(request: Request, components: GatewayComponents = Depends(get_components)) -> None:
    authorization = request.headers.get("Authorization", "")
    presented_key = authorization.removeprefix(BEARER_PREFIX) if authorization.startswith(BEARER_PREFIX) else ""
    if not presented_key or not secrets.compare_digest(presented_key, components.gateway_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_API_KEY_DETAIL)


REPORT_TOKEN_HEADER = "X-Report-Token"
REPORT_TOKEN_QUERY_PARAMETER = "token"
INVALID_REPORT_TOKEN_DETAIL = "missing or invalid report access token"


def get_presented_report_token(request: Request) -> str:
    return request.headers.get(REPORT_TOKEN_HEADER) or request.query_params.get(REPORT_TOKEN_QUERY_PARAMETER) or ""


def require_report_access_token(request: Request, components: GatewayComponents = Depends(get_components)) -> None:
    presented_token = get_presented_report_token(request)
    if not presented_token or not secrets.compare_digest(presented_token, components.report_access_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_REPORT_TOKEN_DETAIL)
