"""Tests for provider config TOML loading and CLI-flag precedence."""

from __future__ import annotations
import os
import argparse
from pathlib import Path

import pytest

import reachy_mini_conversation_app.config as config_mod
import reachy_mini_conversation_app.toml_config as toml_config


@pytest.fixture(autouse=True)
def _isolated_environ() -> None:
    """Restore os.environ after each test, since apply_provider_config mutates it directly."""
    saved = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(saved)


def _args(**overrides: object) -> argparse.Namespace:
    """Build a provider-config args namespace with sensible empty defaults."""
    base = {
        "config": None,
        "provider": None,
        "backend": None,
        "model": None,
        "voice": None,
        "hf_connection_mode": None,
        "hf_ws_url": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_no_config_file_is_a_no_op(tmp_path: Path) -> None:
    """When the config file doesn't exist, os.environ should be untouched."""
    missing = tmp_path / "config.toml"
    os.environ.pop("BACKEND_PROVIDER", None)

    toml_config.apply_provider_config(_args(config=str(missing)))

    assert "BACKEND_PROVIDER" not in os.environ


def test_active_provider_section_populates_env(tmp_path: Path) -> None:
    """The active_provider section should populate the matching env vars."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        active_provider = "openai"

        [providers.openai]
        backend = "openai"
        model = "gpt-realtime-2"
        api_key = "sk-test-123"
        """,
        encoding="utf-8",
    )

    toml_config.apply_provider_config(_args(config=str(config_file)))

    assert os.environ["BACKEND_PROVIDER"] == "openai"
    assert os.environ["MODEL_NAME"] == "gpt-realtime-2"
    assert os.environ["OPENAI_API_KEY"] == "sk-test-123"


def test_explicit_provider_flag_overrides_active_provider(tmp_path: Path) -> None:
    """--provider should select a different section than active_provider."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        active_provider = "openai"

        [providers.openai]
        backend = "openai"

        [providers.gemini]
        backend = "gemini"
        model = "gemini-3.1-flash-live-preview"
        api_key = "gem-test-key"
        """,
        encoding="utf-8",
    )

    toml_config.apply_provider_config(_args(config=str(config_file), provider="gemini"))

    assert os.environ["BACKEND_PROVIDER"] == "gemini"
    assert os.environ["GEMINI_API_KEY"] == "gem-test-key"
    assert "OPENAI_API_KEY" not in os.environ or os.environ.get("OPENAI_API_KEY") != "gem-test-key"


def test_missing_explicit_provider_exits(tmp_path: Path) -> None:
    """Requesting an unknown --provider should fail loudly instead of silently ignoring it."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [providers.openai]
        backend = "openai"
        """,
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        toml_config.apply_provider_config(_args(config=str(config_file), provider="does-not-exist"))


def test_missing_active_provider_warns_but_does_not_exit(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """An unresolvable active_provider (no explicit --provider) should warn, not exit."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        active_provider = "typo"

        [general]
        hf_home = "/tmp/cache"
        """,
        encoding="utf-8",
    )

    toml_config.apply_provider_config(_args(config=str(config_file)))

    assert os.environ["HF_HOME"] == "/tmp/cache"
    assert "BACKEND_PROVIDER" not in os.environ


def test_general_section_maps_to_env_vars(tmp_path: Path) -> None:
    """[general] keys should map to their documented env var names."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [general]
        hf_token = "hf-secret"
        hf_home = "/tmp/hf-cache"
        local_vision_model = "org/model"
        transcription_language = "fr"
        profiles_directory = "/tmp/profiles"
        tools_directory = "/tmp/tools"
        autoload_external_tools = true
        custom_profile = "astronomer"
        """,
        encoding="utf-8",
    )

    toml_config.apply_provider_config(_args(config=str(config_file)))

    assert os.environ["HF_TOKEN"] == "hf-secret"
    assert os.environ["HF_HOME"] == "/tmp/hf-cache"
    assert os.environ["LOCAL_VISION_MODEL"] == "org/model"
    assert os.environ["REALTIME_TRANSCRIPTION_LANGUAGE"] == "fr"
    assert os.environ["REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY"] == "/tmp/profiles"
    assert os.environ["REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY"] == "/tmp/tools"
    assert os.environ["AUTOLOAD_EXTERNAL_TOOLS"] == "True"
    assert os.environ["REACHY_MINI_CUSTOM_PROFILE"] == "astronomer"


def test_api_key_ignored_for_backend_without_api_key_field(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Hugging Face has no api_key env var, so a stray api_key should be ignored with a warning."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [providers.hf]
        backend = "huggingface"
        api_key = "should-be-ignored"
        """,
        encoding="utf-8",
    )

    openai_before = os.environ.get("OPENAI_API_KEY")
    gemini_before = os.environ.get("GEMINI_API_KEY")

    toml_config.apply_provider_config(_args(config=str(config_file), provider="hf"))

    # Neither key should be touched by an api_key meant for a backend that doesn't use one.
    assert os.environ.get("OPENAI_API_KEY") == openai_before
    assert os.environ.get("GEMINI_API_KEY") == gemini_before
    assert any("Ignoring api_key" in record.message for record in caplog.records)


def test_cli_flags_override_toml(tmp_path: Path) -> None:
    """CLI flags must win over the config TOML per the documented precedence."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [providers.openai]
        backend = "openai"
        model = "gpt-realtime-2"
        """,
        encoding="utf-8",
    )

    toml_config.apply_provider_config(
        _args(
            config=str(config_file),
            provider="openai",
            backend="gemini",
            model="gemini-3.1-flash-live-preview",
            hf_connection_mode="local",
            hf_ws_url="ws://127.0.0.1:8765/v1/realtime",
        )
    )

    assert os.environ["BACKEND_PROVIDER"] == "gemini"
    assert os.environ["MODEL_NAME"] == "gemini-3.1-flash-live-preview"
    assert os.environ["HF_REALTIME_CONNECTION_MODE"] == "local"
    assert os.environ["HF_REALTIME_WS_URL"] == "ws://127.0.0.1:8765/v1/realtime"


def test_malformed_toml_exits(tmp_path: Path) -> None:
    """A config file that fails to parse should fail fast with a clear error."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("this is not [valid toml", encoding="utf-8")

    with pytest.raises(SystemExit):
        toml_config.apply_provider_config(_args(config=str(config_file)))


def test_default_config_path_used_when_no_config_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without --config, the default ~/.reachy/config.toml path should be used."""
    default_path = tmp_path / ".reachy" / "config.toml"
    default_path.parent.mkdir(parents=True)
    default_path.write_text(
        """
        [providers.openai]
        backend = "openai"
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(toml_config, "DEFAULT_CONFIG_PATH", default_path)

    toml_config.apply_provider_config(_args(provider="openai"))

    assert os.environ["BACKEND_PROVIDER"] == "openai"


def test_skip_default_config_ignores_default_path_but_not_explicit_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """skip_default_config must skip auto-discovery but still honor an explicit --config."""
    default_path = tmp_path / "default" / "config.toml"
    default_path.parent.mkdir(parents=True)
    default_path.write_text(
        """
        [providers.openai]
        backend = "openai"
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(toml_config, "DEFAULT_CONFIG_PATH", default_path)

    # No --config given, skip_default_config=True: the default path must be ignored.
    toml_config.apply_provider_config(_args(), skip_default_config=True)
    assert "BACKEND_PROVIDER" not in os.environ

    # An explicit --config still wins even with skip_default_config=True.
    explicit_path = tmp_path / "explicit.toml"
    explicit_path.write_text(
        """
        [providers.openai]
        backend = "openai"
        """,
        encoding="utf-8",
    )
    toml_config.apply_provider_config(_args(config=str(explicit_path), provider="openai"), skip_default_config=True)
    assert os.environ["BACKEND_PROVIDER"] == "openai"


def test_refresh_runtime_config_reasserts_toml_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Instance-local .env reloads must not clobber provider config TOML/CLI values."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [providers.openai]
        backend = "openai"
        model = "gpt-realtime-2"
        """,
        encoding="utf-8",
    )

    def fake_parse() -> argparse.Namespace:
        return _args(config=str(config_file), provider="openai")

    monkeypatch.setattr(toml_config, "_parse_known_provider_args", fake_parse)

    # Simulate an instance-local .env that would otherwise win by loading last.
    monkeypatch.setenv("BACKEND_PROVIDER", "gemini")
    monkeypatch.setenv("MODEL_NAME", "gemini-3.1-flash-live-preview")

    config_mod.refresh_runtime_config_from_env()

    assert config_mod.config.BACKEND_PROVIDER == "openai"
    assert config_mod.config.MODEL_NAME == "gpt-realtime-2"
