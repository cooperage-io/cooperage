# Writing Servers for Cooperage

This guide walks you through building an MCP server that runs on Cooperage. If you have an existing MCP server, adapting it is straightforward — you mainly need to containerize it and make it stateless.

## How Cooperage runs your server

When an LLM calls a tool on your server, Cooperage:

1. Starts your Docker image as an ephemeral container
2. Mounts a shared `/workspace` volume (shared across all containers in the session)
3. Proxies the MCP tool call to your container on port 8000
4. Returns the result to the LLM

Your server is one container in a session. Other servers in the same session share the same `/workspace` — this is how multi-tool pipelines work. One server generates data, another analyzes it, all orchestrated by the LLM.

```
LLM → Cooperage Gateway → Your Container (port 8000)
                         → Other Container (port 8000)
                         → ...
                         All share /workspace
```

## Quick start

### 1. Write your server

```python
# server.py
from mcp.server.fastmcp import FastMCP
from cooperage_sdk import workspace, serve

mcp = FastMCP("my-server", json_response=True, stateless_http=True)


@mcp.tool()
def process_data(input_file: str, output_file: str) -> str:
    """Process a data file from the workspace and write results.

    Args:
        input_file: Path to input file (relative to /workspace)
        output_file: Path for output file (relative to /workspace)
    """
    data = workspace.path(input_file).read_text()
    workspace.path(output_file).write_text(data.upper())
    return f"Processed {input_file} → {output_file}"


serve(mcp)
```

That's it. `workspace` handles file I/O and `serve()` handles the HTTP server.

### 2. Create a Dockerfile

```dockerfile
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY server.py .
EXPOSE 8000
CMD ["python", "server.py"]
```

### 3. Create requirements.txt

```
cooperage-sdk
```

This pulls in `mcp` and `uvicorn` — nothing else. Add whatever your server needs (numpy, pandas, Pillow, etc.).

### 4. Hand off to your admin

Once your server is built and tested, give your admin the Docker image. They'll register it with Cooperage and make it available to users:

```bash
docker build -t my-server:latest .
```

See the main [README](../README.md) for registration and deployment details.

## Requirements

Your server must:

| Requirement | Why |
|-------------|-----|
| Use `FastMCP` with `json_response=True, stateless_http=True` | Cooperage proxies HTTP to your container. Stateless mode means no session state on the server side — Cooperage manages sessions. |
| Listen on port 8000 (or set `PORT` env var) | This is the port Cooperage expects. Configurable at registration time. |
| Read/write `/workspace` for data sharing | This is the shared volume. Files written here are visible to all containers in the session and to the LLM via `cooperage_workspace_read`. |

## The Cooperage SDK

The SDK is a separate lightweight package (`pip install cooperage-sdk`) — it does NOT pull in the full Cooperage gateway. It provides two helpers: `workspace` for file I/O and `serve()` for starting the server. Both are optional — you can always use raw pathlib and uvicorn if you prefer.

```python
from cooperage_sdk import workspace, serve
```

### workspace

```python
# Get safe paths — blocks traversal, hides the env var
workspace.path("file.txt")              # returns a Path
workspace.path("file.txt").read_text()  # read text
workspace.path("file.txt").write_text("hello")  # write text

# Works with any library
image = Image.open(workspace.path("photo.png"))
df = pd.read_csv(workspace.path("data.csv"))
plt.savefig(workspace.path("chart.png"))

# Helpers
workspace.exists("output.csv")         # check existence
workspace.list()                        # all files in workspace
workspace.list("reports")              # files in a subdirectory
workspace.root                          # raw Path to /workspace
```

Path traversal is blocked — `workspace.path("../../etc/passwd")` raises `ValueError`.

### serve()

```python
# Replaces the if __name__ + uvicorn boilerplate
serve(mcp)

# Custom host/port if needed
serve(mcp, host="127.0.0.1", port=9000)
```

### Without the SDK

If you don't want to depend on cooperage, you can do everything manually:

```python
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
mcp = FastMCP("my-server", json_response=True, stateless_http=True)

@mcp.tool()
def my_tool(input_file: str) -> str:
    data = (WORKSPACE / input_file).read_text()
    (WORKSPACE / "output.txt").write_text(data.upper())
    return "Done"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)
```

## Writing good tool descriptions

The LLM reads your tool descriptions to decide when and how to call them. Good descriptions make your server more useful.

```python
# Bad — the LLM won't know what format to use or what happens
@mcp.tool()
def analyze(path: str) -> str:
    """Analyze a file."""
    ...

# Good — the LLM knows exactly what to pass and what to expect
@mcp.tool()
def analyze_image(image_path: str) -> str:
    """Analyze an image from the workspace and save a JSON report.

    Computes per-channel color statistics (mean, std, min, max) and
    saves the report as <image_name>_analysis.json in /workspace.

    Args:
        image_path: Path to a PNG or JPEG image (relative to /workspace)
    """
    ...
```

## Handling binary files

If your server produces binary output (images, PDFs, etc.), write them to `/workspace`. The LLM can retrieve them with `cooperage_workspace_read`, which handles base64 encoding automatically.

```python
@mcp.tool()
def generate_chart(data_file: str) -> str:
    """Generate a bar chart from a CSV file.

    Args:
        data_file: Path to CSV file (relative to /workspace)
    """
    import matplotlib.pyplot as plt
    import pandas as pd

    df = pd.read_csv(workspace.path(data_file))
    df.plot(kind="bar")

    out = workspace.path("chart.png")
    plt.savefig(out)
    return "Chart saved to chart.png"
```

## Multiple tools per server

A server can expose as many tools as you want. Group related tools into one server.

```python
mcp = FastMCP("data-tools", json_response=True, stateless_http=True)

@mcp.tool()
def parse_csv(path: str) -> str:
    """Parse a CSV file and return summary statistics."""
    ...

@mcp.tool()
def merge_csvs(paths: list[str], output: str) -> str:
    """Merge multiple CSV files into one."""
    ...

@mcp.tool()
def csv_to_chart(path: str, chart_type: str = "bar") -> str:
    """Generate a chart from a CSV file."""
    ...
```

## Installing system dependencies

If your server needs system packages (ffmpeg, git, etc.), install them in the Dockerfile:

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
# ... rest of Dockerfile
```

## GPU support (coming soon)

Cooperage is adding GPU support for Kubernetes deployments. If your server needs GPU access (ML inference, etc.), you'll be able to request it at registration time. See the [roadmap](../ROADMAP.md) for details.

## Testing your server locally

You can test your server without Cooperage:

```bash
# Set a local workspace directory
export COOPERAGE_WORKSPACE=/tmp/test-workspace
mkdir -p $COOPERAGE_WORKSPACE

# Run the server directly
python server.py

# In another terminal, test with curl
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1}'
```

Or test with Docker to make sure the container works:

```bash
docker build -t my-server:latest .
docker run -p 8000:8000 -v /tmp/test-workspace:/workspace my-server:latest
```

## Adding documentation

Your server probably has domain knowledge the LLM doesn't have — calibration procedures, file format specs, workflow guides, etc. You can expose documentation so the LLM reads it on demand, instead of dumping everything into the tool description.

### Quick way: docs directory

Add a `docs/` folder to your project and call `register_docs(mcp)`. Each file becomes readable by the LLM, with the first line of each file shown as a preview.

```
my-server/
  server.py
  Dockerfile
  docs/
    quickstart.md
    sensor-calibration.md
    output-format.md
```

```python
from cooperage_sdk import workspace, serve, register_docs

mcp = FastMCP("my-server", json_response=True, stateless_http=True)

# ... your tools ...

register_docs(mcp)
serve(mcp)
```

Make sure the `docs/` folder is copied in your Dockerfile:

```dockerfile
COPY docs/ docs/
```

### Manual way: MCP resources

If you want more control — custom descriptions, dynamic content, or non-file resources — register them directly:

```python
@mcp.resource("docs://pipeline-config",
              name="Pipeline Configuration",
              description="How to configure multi-step processing pipelines and set parameters")
def pipeline_docs():
    return Path("docs/pipeline-config.md").read_text()

@mcp.resource("docs://supported-formats",
              name="Supported Formats",
              description="Input/output file formats and their expected schemas")
def format_docs():
    return Path("docs/formats.md").read_text()
```

Both approaches work the same way from the LLM's perspective — it sees a list of available docs with descriptions and reads the ones it needs.

### Tips for writing good docs
- **Start each file with a descriptive first line** — this is shown as a preview so the LLM can decide whether to read the full doc
- **One topic per file** — the LLM reads selectively, not all at once
- Include example inputs and outputs
- Explain domain concepts the LLM won't know

## Examples

| Server | What it does | Source |
|--------|-------------|--------|
| [image-analyzer](../example-servers/image-analyzer/) | Analyzes images using NumPy/PIL, outputs JSON stats | Good starting point for data processing |
| [synthetic-image-generator](../example-servers/synthetic-image-generator/) | Generates procedural satellite imagery | Shows multiple tools + metadata output |

## Checklist

Before registering your server:

- [ ] Tools have clear docstrings describing inputs, outputs, and what they write to `/workspace`
- [ ] All shared data goes through `/workspace` (use `workspace.path()` or raw `/workspace`)
- [ ] Dockerfile builds and runs: `docker build -t name . && docker run -p 8000:8000 name`
