from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gateway.guards.contract import GuardDecision
from gateway.guards.pipeline import GuardMode

DUPLICATE_INSTANCE_ID_ERROR = "guard instance ids must be unique within a checkpoint, duplicated: {instance_ids}"
NO_ENFORCING_INPUT_GUARD_ERROR = "user_input needs at least one guard with mode = \"enforce\""
DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_OLLAMA_URL = "http://ollama:11434"
DEFAULT_CHAT_MODEL_API_KEY_ENV = "CHAT_MODEL_API_KEY"
DEFAULT_DATA_MCP_URL = "http://data-mcp:8001/mcp"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GuardInstanceConfig(StrictModel):
    instance_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    mode: GuardMode = GuardMode.ENFORCE
    timeout_ms: int = Field(1000, gt=0)
    on_error: GuardDecision = GuardDecision.REFUSE
    settings: dict[str, Any] = Field(default_factory=dict)


class CheckpointConfig(StrictModel):
    guards: list[GuardInstanceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_instance_ids(self) -> Self:
        instance_ids = [guard.instance_id for guard in self.guards]
        duplicated = sorted({instance_id for instance_id in instance_ids if instance_ids.count(instance_id) > 1})
        if duplicated:
            raise ValueError(DUPLICATE_INSTANCE_ID_ERROR.format(instance_ids=duplicated))
        return self


class JevAdapter(StrEnum):
    STUB = "stub"
    TYPESAFE = "typesafe"
    GRANITE_GUARDIAN = "granite_guardian"


class JevConfig(StrictModel):
    adapter: JevAdapter = JevAdapter.STUB
    model: str = DEFAULT_JEV_MODEL
    base_url: str = DEFAULT_OLLAMA_URL  # Ollama server, used by the granite_guardian adapter only.


class ChatModelAdapter(StrEnum):
    STUB = "stub"
    OPENAI_COMPATIBLE = "openai_compatible"


class ChatModelConfig(StrictModel):
    adapter: ChatModelAdapter = ChatModelAdapter.STUB
    base_url: str = ""
    model: str = ""
    api_key_env: str = DEFAULT_CHAT_MODEL_API_KEY_ENV
    max_tool_rounds: int = Field(8, ge=1)
    request_timeout_seconds: float = Field(60.0, gt=0)


class DataMcpConfig(StrictModel):
    url: str = DEFAULT_DATA_MCP_URL


class GatewayConfig(StrictModel):
    user_input: CheckpointConfig = Field(default_factory=CheckpointConfig)
    jev: JevConfig = Field(default_factory=JevConfig)
    chat_model: ChatModelConfig = Field(default_factory=ChatModelConfig)
    data_mcp: DataMcpConfig = Field(default_factory=DataMcpConfig)

    @model_validator(mode="after")
    def require_enforcing_user_input_guard(self) -> Self:
        # An empty or half-saved file is valid TOML; accepting it would hot-reload a checkpoint that allows everything.
        if not any(guard.mode is GuardMode.ENFORCE for guard in self.user_input.guards):
            raise ValueError(NO_ENFORCING_INPUT_GUARD_ERROR)
        return self


@dataclass(frozen=True)
class GatewayConfigVersion:
    version_id: str
    config: GatewayConfig
    author: str
    created_at: datetime
