# Contributing to Cooperage

## Setup

```bash
git clone https://github.com/cooperage-io/cooperage
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
  image-analyzer/            # example: analyze images with numpy/PIL
  synthetic-image-generator/ # example: generate synthetic satellite imagery
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

## Versioning

Cooperage follows [Semantic Versioning](https://semver.org/). The version appears in two places that must stay in sync:

- `pyproject.toml` → `project.version`
- `chart/Chart.yaml` → `version` and `appVersion`

### Cutting a release

1. Run the bump script from main:
   ```bash
   ./scripts/bump-version.sh 0.2.0
   ```
   This updates both files, commits, and tags in one step.
2. Push:
   ```bash
   git push origin main --tags
   ```

Tags trigger CI to publish versioned images to `ghcr.io/cooperage-io/` (e.g. `cooperage:0.2.0` alongside `cooperage:latest`).

## Submitting changes

- Keep PRs focused — one thing at a time
- Tests required for gateway or orchestrator changes
- Run ruff before pushing
