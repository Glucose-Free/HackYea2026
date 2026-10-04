from dataclasses import dataclass

from gateway.audit.log import AuditLog
from gateway.audit.query import AuditQuery
from gateway.core.gateway import Gateway
from gateway.identity.resolver import IdentityResolver


@dataclass(frozen=True)
class GatewayComponents:
    gateway: Gateway
    audit_log: AuditLog
    identity_resolver: IdentityResolver
    gateway_api_key: str
    audit_query: AuditQuery
    report_access_token: str
