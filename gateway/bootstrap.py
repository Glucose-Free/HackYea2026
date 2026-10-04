import logging
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from typesafe_sdk import AsyncTypeSafeClient

from gateway.agent.chat_agent import ChatAgent
from gateway.agent.chat_model import ChatModel, OpenAiCompatibleChatModel, StubChatModel
from gateway.agent.tools import McpToolProvider
from gateway.audit.log import AuditAnchorFile, AuditLog
from gateway.audit.query import JsonlAuditQuery
from gateway.components import GatewayComponents
from gateway.config.model import ChatModelAdapter, ChatModelConfig, JevAdapter, JevConfig
from gateway.config.provider import PipelineProvider, build_config_validator
from gateway.config.store import FileConfigStore, parse_config
from gateway.core.gateway import Gateway
from gateway.guards.contract import GuardDependencies
from gateway.guards.registry import GuardRegistry
from gateway.identity.resolver import OpenWebUiHeaderResolver
from gateway.policy.rules_admin import PolicyRulesAdmin
from gateway.jev.client import GraniteGuardianJevClient, JevClient, StubJevClient, TypeSafeJevClient

GATEWAY_CONFIG_PATH_ENV = "GATEWAY_CONFIG_PATH"
GATEWAY_CONFIG_HISTORY_DIR_ENV = "GATEWAY_CONFIG_HISTORY_DIR"
AUDIT_LOG_PATH_ENV = "AUDIT_LOG_PATH"
AUDIT_ANCHOR_PATH_ENV = "AUDIT_ANCHOR_PATH"
GATEWAY_API_KEY_ENV = "GATEWAY_API_KEY"
REPORT_ACCESS_TOKEN_ENV = "REPORT_ACCESS_TOKEN"
POLICY_RULES_PATH_ENV = "POLICY_RULES_PATH"
POLICY_DEFAULT_RULES_PATH_ENV = "POLICY_DEFAULT_RULES_PATH"
DEFAULT_CONFIG_PATH = "config/gateway.toml"
DEFAULT_CONFIG_HISTORY_DIR = "data/config_history"
DEFAULT_AUDIT_LOG_PATH = "data/audit.jsonl"
DEFAULT_AUDIT_ANCHOR_PATH = "data/audit.anchor.json"
DEFAULT_POLICY_DEFAULT_RULES_PATH = "examples/bank_demo_rules.json"
MISSING_ENV_ERROR = "environment variable {name} must be set"
# The defaults in docker-compose.yml and .env.example; they are public, so anyone can use them.
PUBLISHED_DEMO_SECRETS = frozenset({"demo-gateway-key", "demo-report-token"})
DEMO_SECRET_WARNING = (
    "%s is set to a published demo value: anyone who reads the repo can use it. "
    "Set a random value in .env before exposing this gateway beyond a local demo."
)

logger = logging.getLogger(__name__)


def get_required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(MISSING_ENV_ERROR.format(name=name))
    return value


def get_required_secret_env(name: str) -> str:
    # Warn rather than refuse, so `docker compose up` keeps working with no setup.
    value = get_required_env(name)
    if value in PUBLISHED_DEMO_SECRETS:
        logger.warning(DEMO_SECRET_WARNING, name)
    return value


async def open_jev_client(jev_config: JevConfig, exit_stack: AsyncExitStack) -> JevClient:
    if jev_config.adapter is JevAdapter.STUB:
        return StubJevClient()
    if jev_config.adapter is JevAdapter.GRANITE_GUARDIAN:
        # No client-side timeout: each guard's timeout_ms bounds the call and decides on_error.
        http_client = await exit_stack.enter_async_context(httpx.AsyncClient(timeout=None))
        return GraniteGuardianJevClient(http_client, jev_config.base_url, jev_config.model)
    sdk_client = await exit_stack.enter_async_context(AsyncTypeSafeClient(model=jev_config.model))
    return TypeSafeJevClient(sdk_client, jev_config.model)


async def open_chat_model(chat_model_config: ChatModelConfig, exit_stack: AsyncExitStack) -> ChatModel:
    if chat_model_config.adapter is ChatModelAdapter.STUB:
        return StubChatModel()
    http_client = await exit_stack.enter_async_context(httpx.AsyncClient(timeout=chat_model_config.request_timeout_seconds))
    return OpenAiCompatibleChatModel(
        http_client, chat_model_config.base_url, chat_model_config.model, get_required_env(chat_model_config.api_key_env),
    )


def build_policy_rules_admin() -> PolicyRulesAdmin | None:
    rules_path = os.environ.get(POLICY_RULES_PATH_ENV, "").strip()
    if not rules_path:
        return None
    default_rules_path = os.environ.get(POLICY_DEFAULT_RULES_PATH_ENV, DEFAULT_POLICY_DEFAULT_RULES_PATH)
    return PolicyRulesAdmin(Path(rules_path), Path(default_rules_path))


@asynccontextmanager
async def open_components_from_environment() -> AsyncIterator[GatewayComponents]:
    gateway_api_key = get_required_secret_env(GATEWAY_API_KEY_ENV)
    report_access_token = get_required_secret_env(REPORT_ACCESS_TOKEN_ENV)
    config_path = Path(os.environ.get(GATEWAY_CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))
    # Adapters are built once from the startup config; only guard pipelines hot-reload.
    startup_config = parse_config(config_path.read_bytes())
    async with AsyncExitStack() as exit_stack:
        guard_dependencies = GuardDependencies(jev_client=await open_jev_client(startup_config.jev, exit_stack))
        registry = GuardRegistry.load_from_entry_points()
        store = FileConfigStore(
            config_path,
            Path(os.environ.get(GATEWAY_CONFIG_HISTORY_DIR_ENV, DEFAULT_CONFIG_HISTORY_DIR)),
            build_config_validator(registry, guard_dependencies),
        )
        chat_agent = ChatAgent(
            await open_chat_model(startup_config.chat_model, exit_stack),
            McpToolProvider(startup_config.data_mcp.url),
            startup_config.chat_model.max_tool_rounds,
        )
        audit_log = AuditLog(
            Path(os.environ.get(AUDIT_LOG_PATH_ENV, DEFAULT_AUDIT_LOG_PATH)),
            AuditAnchorFile(Path(os.environ.get(AUDIT_ANCHOR_PATH_ENV, DEFAULT_AUDIT_ANCHOR_PATH))),
        )
        yield GatewayComponents(
            gateway=Gateway(PipelineProvider(store, registry, guard_dependencies), chat_agent, audit_log),
            audit_log=audit_log,
            identity_resolver=OpenWebUiHeaderResolver(),
            gateway_api_key=gateway_api_key,
            audit_query=JsonlAuditQuery(audit_log),
            report_access_token=report_access_token,
            policy_rules_admin=build_policy_rules_admin(),
        )
