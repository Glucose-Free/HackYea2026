from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from gateway.api.dependencies import get_components, require_report_access_token
from gateway.audit.query import (
    FetchTotalsBucket,
    InvalidCursorError,
    InvalidTimeRangeError,
    PageRequest,
    RequestFilters,
    RequestPage,
    RequestStatus,
    RequestStatusCounts,
    RequestTrace,
    TimeBucket,
    UserFetchStats,
    build_time_range,
)
from gateway.components import GatewayComponents
from gateway.core.reply import DeniedAt, ReplyOutcome

TRACE_NOT_FOUND_DETAIL = "no request with id {request_id!r}"
MAX_PAGE_LIMIT = 500

router = APIRouter(prefix="/admin", dependencies=[Depends(require_report_access_token)])


@router.get("/fetches/totals")
def get_fetch_totals(
    start: datetime | None = None,
    end: datetime | None = None,
    bucket: TimeBucket = TimeBucket.HOUR,
    user_id: str | None = None,
    components: GatewayComponents = Depends(get_components),
) -> list[FetchTotalsBucket]:
    try:
        return components.audit_query.get_fetch_totals(build_time_range(start, end), bucket, user_id=user_id)
    except InvalidTimeRangeError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error


@router.get("/users/fetch-stats")
def list_user_fetch_stats(
    start: datetime | None = None,
    end: datetime | None = None,
    components: GatewayComponents = Depends(get_components),
) -> list[UserFetchStats]:
    try:
        return components.audit_query.list_user_fetch_stats(build_time_range(start, end))
    except InvalidTimeRangeError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error


@router.get("/requests")
def list_requests(
    user_id: str | None = None,
    outcome: ReplyOutcome | None = None,
    denied_at: DeniedAt | None = None,
    request_status: RequestStatus | None = Query(None, alias="status"),
    start: datetime | None = None,
    end: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    components: GatewayComponents = Depends(get_components),
) -> RequestPage:
    try:
        time_range = build_time_range(start, end) if start is not None or end is not None else None
        filters = RequestFilters(user_id=user_id, outcome=outcome, denied_at=denied_at, status=request_status, time_range=time_range)
        return components.audit_query.list_requests(filters, PageRequest(cursor=cursor, limit=limit))
    except (InvalidTimeRangeError, InvalidCursorError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error


@router.get("/requests/status-counts")
def get_request_status_counts(
    start: datetime | None = None,
    end: datetime | None = None,
    components: GatewayComponents = Depends(get_components),
) -> RequestStatusCounts:
    try:
        return components.audit_query.get_request_status_counts(build_time_range(start, end))
    except InvalidTimeRangeError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error


@router.get("/requests/{request_id}/trace")
def get_request_trace(request_id: str, components: GatewayComponents = Depends(get_components)) -> RequestTrace:
    trace = components.audit_query.get_request_trace(request_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, TRACE_NOT_FOUND_DETAIL.format(request_id=request_id))
    return trace
