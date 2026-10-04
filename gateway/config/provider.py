import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError

from gateway.config.model import GatewayConfig, GatewayConfigVersion, GuardInstanceConfig
from gateway.config.store import ConfigStore, ConfigValidationError, ConfigValidator
from gateway.core.conversation import Conversation
from gateway.guards.contract import GuardDependencies, MissingGuardDependencyError
from gateway.guards.pipeline import ConfiguredGuard, GuardPipeline
from gateway.guards.registry import GuardRegistry, UnknownGuardTypeError

VALIDATION_VERSION_ID = "validation"
VALIDATION_AUTHOR = "validator"
GUARD_BUILD_ERROR = "guard {instance_id!r}: {error}"
RELOAD_FAILED_MESSAGE = "config version %s could not be built; keeping version %s"
STORE_READ_FAILED_MESSAGE = "could not read active config version; keeping version %s"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckpointPipelines:
    config_version: str
    user_input: GuardPipeline[Conversation]


def build_configured_guard(
    instance_config: GuardInstanceConfig,
    registry: GuardRegistry,
    dependencies: GuardDependencies,
) -> ConfiguredGuard:
    try:
        guard_class = registry.get_guard_class(instance_config.type)
        settings = guard_class.settings_model.model_validate(instance_config.settings)
        guard = guard_class.create(settings, dependencies)
    except (UnknownGuardTypeError, ValidationError, MissingGuardDependencyError) as error:
        raise ConfigValidationError(GUARD_BUILD_ERROR.format(instance_id=instance_config.instance_id, error=error)) from error
    return ConfiguredGuard(
        instance_id=instance_config.instance_id,
        guard=guard,
        mode=instance_config.mode,
        timeout_seconds=instance_config.timeout_ms / 1000,
        on_error=instance_config.on_error,
    )


def build_checkpoint_pipelines(
    version: GatewayConfigVersion,
    registry: GuardRegistry,
    dependencies: GuardDependencies,
) -> CheckpointPipelines:
    user_input_guards = [
        build_configured_guard(instance_config, registry, dependencies)
        for instance_config in version.config.user_input.guards
    ]
    return CheckpointPipelines(version.version_id, GuardPipeline(user_input_guards))


def build_config_validator(registry: GuardRegistry, dependencies: GuardDependencies) -> ConfigValidator:
    def validate(config: GatewayConfig) -> None:
        validation_version = GatewayConfigVersion(VALIDATION_VERSION_ID, config, VALIDATION_AUTHOR, datetime.now(UTC))
        build_checkpoint_pipelines(validation_version, registry, dependencies)

    return validate


class PipelineProvider:
    """Builds pipelines from the active config version and swaps them when the version changes."""

    def __init__(self, store: ConfigStore, registry: GuardRegistry, dependencies: GuardDependencies):
        self._store = store
        self._registry = registry
        self._dependencies = dependencies
        self._current = build_checkpoint_pipelines(store.get_active(), registry, dependencies)
        self._last_failed_version_id: str | None = None

    def get_current(self) -> CheckpointPipelines:
        try:
            active_version_id = self._store.get_active_version_id()
        except OSError:
            logger.exception(STORE_READ_FAILED_MESSAGE, self._current.config_version)
            return self._current
        if active_version_id not in (self._current.config_version, self._last_failed_version_id):
            self._reload(active_version_id)
        return self._current

    def _reload(self, active_version_id: str) -> None:
        try:
            self._current = build_checkpoint_pipelines(self._store.get_active(), self._registry, self._dependencies)
            self._last_failed_version_id = None
        except (ConfigValidationError, OSError):
            # Remember the failure so a broken file is logged once, not on every request.
            self._last_failed_version_id = active_version_id
            logger.exception(RELOAD_FAILED_MESSAGE, active_version_id, self._current.config_version)
