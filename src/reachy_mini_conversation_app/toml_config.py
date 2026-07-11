"""Provider configuration loaded from ~/.reachy/config.toml and CLI flags.

Precedence (highest wins): CLI flags > provider config TOML > existing
environment variables / .env > built-in defaults. `apply_provider_config()`
layers the TOML file and any CLI overrides onto `os.environ` so that
`config.py`'s plain `os.getenv(...)` reads pick up the right values no
matter which entrypoint imports it first.

This module intentionally has no dependency on `config.py` or `utils.py`
(which pulls in camera/vision imports) so importing it stays lightweight.
"""

import os
import sys
import logging
import argparse
from typing import Any
from pathlib import Path


if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".reachy" / "config.toml"

BACKEND_CHOICES = ["openai", "gemini", "huggingface"]

# [providers.<name>] keys -> Config env var names.
_PROVIDER_ENV_MAP = {
    "backend": "BACKEND_PROVIDER",
    "model": "MODEL_NAME",
    "connection_mode": "HF_REALTIME_CONNECTION_MODE",
    "ws_url": "HF_REALTIME_WS_URL",
}

# [general] keys -> Config env var names.
_GENERAL_ENV_MAP = {
    "hf_token": "HF_TOKEN",
    "hf_home": "HF_HOME",
    "local_vision_model": "LOCAL_VISION_MODEL",
    "transcription_language": "REALTIME_TRANSCRIPTION_LANGUAGE",
    "profiles_directory": "REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY",
    "tools_directory": "REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY",
    "autoload_external_tools": "AUTOLOAD_EXTERNAL_TOOLS",
    "custom_profile": "REACHY_MINI_CUSTOM_PROFILE",
}

# provider "backend" value -> the env var its "api_key" field should populate.
_API_KEY_ENV_BY_BACKEND = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def add_provider_config_args(parser: argparse.ArgumentParser) -> None:
    """Register provider-config CLI flags on *parser* (shared by the app parser and our own)."""
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help=f"Path to a provider config TOML file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        metavar="NAME",
        help="Activate the [providers.NAME] section from the config TOML for this run",
    )
    parser.add_argument(
        "--backend",
        choices=BACKEND_CHOICES,
        default=None,
        help="Override the realtime backend provider for this run",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        metavar="MODEL_NAME",
        help="Override the model name for this run",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        metavar="VOICE",
        help="Override the assistant voice for this run",
    )
    parser.add_argument(
        "--hf-connection-mode",
        choices=["local", "deployed"],
        default=None,
        dest="hf_connection_mode",
        help="Override the Hugging Face realtime connection mode for this run",
    )
    parser.add_argument(
        "--hf-ws-url",
        type=str,
        default=None,
        dest="hf_ws_url",
        metavar="URL",
        help="Override the direct Hugging Face realtime websocket URL for this run",
    )


def _parse_known_provider_args() -> argparse.Namespace:
    """Parse just the provider-config flags out of sys.argv, ignoring everything else."""
    parser = argparse.ArgumentParser(add_help=False)
    add_provider_config_args(parser)
    args, _ = parser.parse_known_args()
    return args


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file, exiting with a clear error if it exists but is invalid."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
            return data
    except Exception as exc:
        logger.critical("Failed to parse provider config TOML at %s: %s", path, exc)
        sys.exit(1)


def _resolve_provider_section(data: dict[str, Any], requested_provider: str | None) -> dict[str, Any]:
    """Return the selected [providers.NAME] table, if any is configured."""
    providers: dict[str, Any] = data.get("providers", {})
    name = requested_provider or data.get("active_provider")
    if not name:
        return {}

    section: dict[str, Any] | None = providers.get(name)
    if section is None:
        available = sorted(providers)
        message = f"Provider {name!r} not found in provider config. Available: {available}."
        if requested_provider is not None:
            logger.critical(message)
            sys.exit(1)
        logger.warning(message)
        return {}
    return section


def apply_provider_config(
    cli_args: argparse.Namespace | None = None,
    *,
    skip_default_config: bool = False,
) -> None:
    """Layer the provider config TOML and CLI overrides onto os.environ.

    Call this after `.env` has already been loaded and before reading any
    Config fields via `os.getenv`. Safe to call multiple times (e.g. again
    after an instance-local `.env` reload) since it always re-derives from
    the same TOML file and CLI flags.

    `skip_default_config` skips auto-discovering `~/.reachy/config.toml`
    (mirrors REACHY_MINI_SKIP_DOTENV, for reproducible test/CI runs) but an
    explicit `--config` path and CLI overrides (--backend/--model/...) are
    still honored, since those are explicit per-invocation choices rather
    than implicit machine-specific state.
    """
    args = cli_args if cli_args is not None else _parse_known_provider_args()

    explicit_config = getattr(args, "config", None)
    if explicit_config:
        config_path: Path | None = Path(explicit_config).expanduser()
    elif skip_default_config:
        config_path = None
    else:
        config_path = DEFAULT_CONFIG_PATH

    data = _load_toml(config_path) if config_path is not None else {}
    if data:
        logger.info("Loaded provider config from %s", config_path)

    provider_section = _resolve_provider_section(data, getattr(args, "provider", None))
    general_section = data.get("general", {})
    backend_hint = provider_section.get("backend")

    for key, env_name in _GENERAL_ENV_MAP.items():
        if general_section.get(key) is not None:
            os.environ[env_name] = str(general_section[key])

    for key, env_name in _PROVIDER_ENV_MAP.items():
        if provider_section.get(key) is not None:
            os.environ[env_name] = str(provider_section[key])

    if provider_section.get("api_key"):
        api_key_env = _API_KEY_ENV_BY_BACKEND.get(backend_hint) if isinstance(backend_hint, str) else None
        if api_key_env:
            os.environ[api_key_env] = str(provider_section["api_key"])
        else:
            logger.warning(
                "Ignoring api_key in provider config: backend %r does not use an api_key field.",
                backend_hint,
            )

    # CLI flags win over everything above.
    if getattr(args, "backend", None):
        os.environ["BACKEND_PROVIDER"] = args.backend
    if getattr(args, "model", None):
        os.environ["MODEL_NAME"] = args.model
    if getattr(args, "hf_connection_mode", None):
        os.environ["HF_REALTIME_CONNECTION_MODE"] = args.hf_connection_mode
    if getattr(args, "hf_ws_url", None):
        os.environ["HF_REALTIME_WS_URL"] = args.hf_ws_url
