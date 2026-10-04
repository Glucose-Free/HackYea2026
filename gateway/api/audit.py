from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from gateway.api.dependencies import get_components, get_presented_report_token, require_report_access_token
from gateway.audit.query import PageRequest, RequestFilters, TimeBucket, build_time_range
from gateway.audit.report import build_incident_page, build_report_page
from gateway.components import GatewayComponents

RECENT_DENIED_LIMIT = 20
REQUEST_NOT_FOUND_DETAIL = "no request with id {request_id!r}"

public_router = APIRouter()
protected_router = APIRouter(dependencies=[Depends(require_report_access_token)])


@public_router.get("/audit/verify")
def verify_audit_chain(components: GatewayComponents = Depends(get_components)) -> dict[str, Any]:
    chain = components.audit_log.verify_chain()
    return {"intact": chain.intact, "checked": chain.checked, "first_broken": chain.first_broken, "head": chain.head}


@protected_router.get("/audit")
def list_audit_events(components: GatewayComponents = Depends(get_components)) -> list[dict[str, Any]]:
    return components.audit_log.read_events()


@protected_router.get("/report", response_class=HTMLResponse)
def get_report(request: Request, components: GatewayComponents = Depends(get_components)) -> str:
    time_range = build_time_range(None, None)
    query = components.audit_query
    recent_problems = query.list_requests(
        RequestFilters(time_range=time_range, only_problems=True), PageRequest(limit=RECENT_DENIED_LIMIT),
    )
    return build_report_page(
        components.audit_log.verify_chain(),
        query.get_fetch_totals(time_range, TimeBucket.HOUR),
        query.list_user_fetch_stats(time_range),
        recent_problems,
        get_presented_report_token(request),
    )


@protected_router.get("/report/incident/{request_id}", response_class=HTMLResponse)
def get_incident(request_id: str, components: GatewayComponents = Depends(get_components)) -> str:
    trace = components.audit_query.get_request_trace(request_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, REQUEST_NOT_FOUND_DETAIL.format(request_id=request_id))
    return build_incident_page(trace)
