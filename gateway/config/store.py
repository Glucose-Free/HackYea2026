import hashlib
import os
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import tomli_w
from pydantic import ValidationError

from gateway.config.model import GatewayConfig, GatewayConfigVersion

FILE_STORE_AUTHOR = "file"
HISTORY_FILE_SUFFIX = ".toml"
VERSION_ID_LENGTH = 12
INVALID_TOML_ERROR = "config is not valid TOML: {error}"
INVALID_CONFIG_ERROR = "config does not match the schema: {error}"
UNKNOWN_VERSION_ERROR = "no archived config version {version_id!r}"

ConfigValidator = Callable[[GatewayConfig], None]


class ConfigValidationError(ValueError):
    pass


class ConfigStore(Protocol):
    def get_active_version_id(self) -> str: ...
    def get_active(self) -> GatewayConfigVersion: ...
    def save(self, config: GatewayConfig, author: str) -> GatewayConfigVersion: ...
    def list_history(self) -> list[GatewayConfigVersion]: ...
    def activate(self, version_id: str, author: str) -> GatewayConfigVersion: ...


def compute_version_id(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:VERSION_ID_LENGTH]


def parse_config(content: bytes) -> GatewayConfig:
    try:
        raw_config = tomllib.loads(content.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigValidationError(INVALID_TOML_ERROR.format(error=error)) from error
    try:
        return GatewayConfig.model_validate(raw_config)
    except ValidationError as error:
        raise ConfigValidationError(INVALID_CONFIG_ERROR.format(error=error)) from error


def serialize_config(config: GatewayConfig) -> bytes:
    return tomli_w.dumps(config.model_dump(mode="json", exclude_none=True)).encode("utf-8")


class FileConfigStore:
    """Versioned config on disk: the active file plus archived copies named by version id."""

    def __init__(self, active_path: Path, history_dir: Path, validate: ConfigValidator):
        self._active_path = active_path
        self._history_dir = history_dir
        self._validate = validate

    def get_active_version_id(self) -> str:
        return compute_version_id(self._active_path.read_bytes())

    def get_active(self) -> GatewayConfigVersion:
        return self._load_version(self._active_path, FILE_STORE_AUTHOR)

    def save(self, config: GatewayConfig, author: str) -> GatewayConfigVersion:
        self._validate(config)
        content = serialize_config(config)
        self._replace_active_content(content)
        return GatewayConfigVersion(compute_version_id(content), config, author, datetime.now(UTC))

    def list_history(self) -> list[GatewayConfigVersion]:
        if not self._history_dir.exists():
            return []
        archived_paths = sorted(self._history_dir.glob(f"*{HISTORY_FILE_SUFFIX}"), key=lambda path: path.stat().st_mtime)
        return [self._load_version(path, FILE_STORE_AUTHOR, validate=False) for path in archived_paths]

    def activate(self, version_id: str, author: str) -> GatewayConfigVersion:
        archived_path = self._history_dir / f"{version_id}{HISTORY_FILE_SUFFIX}"
        if not archived_path.exists():
            raise ConfigValidationError(UNKNOWN_VERSION_ERROR.format(version_id=version_id))
        version = self._load_version(archived_path, author)
        self._replace_active_content(archived_path.read_bytes())
        return version

    def _load_version(self, path: Path, author: str, validate: bool = True) -> GatewayConfigVersion:
        content = path.read_bytes()
        config = parse_config(content)
        if validate:
            self._validate(config)
        created_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        return GatewayConfigVersion(compute_version_id(content), config, author, created_at)

    def _replace_active_content(self, content: bytes) -> None:
        self._archive_active()
        temporary_path = self._active_path.with_suffix(".tmp")
        temporary_path.write_bytes(content)
        os.replace(temporary_path, self._active_path)

    def _archive_active(self) -> None:
        self._history_dir.mkdir(parents=True, exist_ok=True)
        current_content = self._active_path.read_bytes()
        archived_path = self._history_dir / f"{compute_version_id(current_content)}{HISTORY_FILE_SUFFIX}"
        archived_path.write_bytes(current_content)
