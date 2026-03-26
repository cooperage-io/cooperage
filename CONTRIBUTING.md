# Contributing to Cooperage

## Setup

```bash
git clone https://github.com/EvanLavizadeh/cooperage
cd cooperage
uv sync
```

## Running tests

```bash
uv run pytest
```

All tests are mocked — no Docker daemon or Kubernetes cluster required.

## Linting

```bash
uv run ruff check cooperage/
uv run ruff check --fix cooperage/  # auto-fix
```

## Project structure

```
cooperage/          # core package
  cli/              # cooperage CLI (typer)
  core/             # models, config
  gateway/          # MCP gateway server
  orchestrator/     # docker.py, kubernetes.py
  registry/         # server registration
  session/          # session + container lifecycle
servers/
  workspace/        # built-in workspace server (auto-registered by gateway)
example-servers/
  analysis/         # example: run Python scripts
  simulator/        # example: generate synthetic imagery
tests/
```

## Adding an example server

1. Create `example-servers/<name>/server.py` using FastMCP:
    ```python
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("my-server", json_response=True, stateless_http=True)

    @mcp.tool()
    def my_tool(arg: str) -> str:
        """Tool description."""
        ...

    if __name__ == "__main__":
        import os, uvicorn
        uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    ```
2. Add a `Dockerfile` and `requirements.txt`
3. Build: `docker build -t my-server:latest example-servers/<name>`
4. Register: `cooperage register --name <name> --image my-server:latest`

## Submitting changes

- Keep PRs focused — one thing at a time
- Tests required for gateway or orchestrator changes
- Run ruff before pushing
