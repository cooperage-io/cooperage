"""
Adapter configuration models — shared between the CLI and the adapter server.

Defines the YAML/JSON schema for wrapping REST APIs, LangChain tools,
and Python functions as MCP servers.
"""

from enum import Enum

from pydantic import BaseModel, Field


class AdapterType(str, Enum):
    REST_API = "rest-api"
    LANGCHAIN = "langchain"
    PYTHON = "python"


class ParamLocation(str, Enum):
    QUERY = "query"
    BODY = "body"
    HEADER = "header"
    PATH = "path"


class ParamDef(BaseModel):
    type: str = "string"
    description: str = ""
    required: bool = True
    location: ParamLocation | None = None  # None = infer from HTTP method
    default: str | int | float | bool | None = None


class AuthConfig(BaseModel):
    type: str = "none"  # none, bearer, api-key, basic
    token: str | None = None  # for bearer — use ${ENV_VAR} syntax
    api_key: str | None = None  # for api-key
    api_key_header: str = "X-API-Key"
    username: str | None = None  # for basic
    password: str | None = None  # for basic


class RestToolDef(BaseModel):
    name: str
    description: str = ""
    method: str = "GET"
    path: str  # e.g. /forecast, /users/{user_id}
    params: dict[str, ParamDef] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class LangChainToolRef(BaseModel):
    name: str  # name of the tool to expose (must match the @tool name or class name)


class PythonToolDef(BaseModel):
    name: str
    function: str  # function name in the module
    description: str = ""
    params: dict[str, ParamDef] = Field(default_factory=dict)


class AdapterConfig(BaseModel):
    name: str
    type: AdapterType
    description: str = ""

    # REST API fields
    base_url: str | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    default_headers: dict[str, str] = Field(default_factory=dict)

    # LangChain / Python fields
    source: str | None = None  # file path or module import path
    package: str | None = None  # pip package to install at startup

    # Tools (polymorphic based on type)
    rest_tools: list[RestToolDef] = Field(default_factory=list, alias="tools")
    langchain_tools: list[str] = Field(default_factory=list)  # tool names to expose (empty = all)
    python_tools: list[PythonToolDef] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
