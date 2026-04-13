"""
Adapter config model tests — parsing, validation, defaults.
"""


from cooperage.adapter.config import (
    AdapterConfig, AdapterType, AuthConfig, ParamDef, ParamLocation, RestToolDef,
)


# ── REST API config ──────────────────────────────────────────────────────────


def test_parse_rest_api_config():
    data = {
        "name": "weather",
        "type": "rest-api",
        "base_url": "https://api.weather.com/v1",
        "description": "Weather API",
        "tools": [
            {
                "name": "get_forecast",
                "description": "Get forecast",
                "method": "GET",
                "path": "/forecast",
                "params": {
                    "lat": {"type": "number", "description": "Latitude"},
                    "lon": {"type": "number", "description": "Longitude"},
                },
            },
        ],
    }
    config = AdapterConfig(**data)
    assert config.name == "weather"
    assert config.type == AdapterType.REST_API
    assert config.base_url == "https://api.weather.com/v1"
    assert len(config.rest_tools) == 1
    assert config.rest_tools[0].name == "get_forecast"
    assert config.rest_tools[0].method == "GET"
    assert "lat" in config.rest_tools[0].params


def test_rest_tool_defaults():
    tool = RestToolDef(name="test", path="/test")
    assert tool.method == "GET"
    assert tool.description == ""
    assert tool.params == {}
    assert tool.headers == {}


def test_param_location_default_is_none():
    param = ParamDef(type="string")
    assert param.location is None  # inferred at runtime from method


def test_param_explicit_location():
    param = ParamDef(type="string", location=ParamLocation.BODY)
    assert param.location == ParamLocation.BODY


# ── Auth config ──────────────────────────────────────────────────────────────


def test_auth_defaults_to_none():
    auth = AuthConfig()
    assert auth.type == "none"


def test_auth_bearer():
    auth = AuthConfig(type="bearer", token="${API_KEY}")
    assert auth.token == "${API_KEY}"


def test_auth_api_key():
    auth = AuthConfig(type="api-key", api_key="${MY_KEY}", api_key_header="X-Custom")
    assert auth.api_key_header == "X-Custom"


def test_auth_basic():
    auth = AuthConfig(type="basic", username="${USER}", password="${PASS}")
    assert auth.username == "${USER}"


# ── LangChain config ────────────────────────────────────────────────────────


def test_parse_langchain_config():
    data = {
        "name": "my-agents",
        "type": "langchain",
        "source": "/workspace/tools.py",
        "langchain_tools": ["search_web", "analyze"],
    }
    config = AdapterConfig(**data)
    assert config.type == AdapterType.LANGCHAIN
    assert config.source == "/workspace/tools.py"
    assert config.langchain_tools == ["search_web", "analyze"]


def test_langchain_with_package():
    data = {
        "name": "pkg-tools",
        "type": "langchain",
        "source": "my_package.tools",
        "package": "my-package==1.0.0",
    }
    config = AdapterConfig(**data)
    assert config.package == "my-package==1.0.0"


# ── Serialization ────────────────────────────────────────────────────────────


def test_config_round_trip():
    data = {
        "name": "test",
        "type": "rest-api",
        "base_url": "http://localhost:5000",
        "auth": {"type": "bearer", "token": "${TOKEN}"},
        "tools": [{"name": "ping", "path": "/ping"}],
    }
    config = AdapterConfig(**data)
    dumped = config.model_dump_json()
    reloaded = AdapterConfig.model_validate_json(dumped)
    assert reloaded.name == "test"
    assert reloaded.auth.token == "${TOKEN}"


# ── Validation errors ──────────────────────────────────────────────────────


def test_rest_api_without_base_url_raises():
    """rest-api type without base_url must raise ValueError."""
    import pytest
    with pytest.raises(ValueError, match="base_url"):
        AdapterConfig(name="bad", type="rest-api")


def test_langchain_without_source_raises():
    """langchain type without source must raise ValueError."""
    import pytest
    with pytest.raises(ValueError, match="source"):
        AdapterConfig(name="bad", type="langchain")
