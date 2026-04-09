# Writing Servers for Cooperage

This guide walks you through building a tool server that runs on Cooperage.

## How it works

Cooperage runs your code in a Docker container. When the LLM calls one of your tools, Cooperage starts your container, passes the request, and returns the result. All containers in the same session share a `/workspace` folder — one tool can write a file, another can read it.

You write the tools. Cooperage handles everything else.

---

## With the Cooperage SDK (recommended)

The SDK handles the boilerplate so you can focus on your tools.

### 1. Write your server

```python
# server.py
from mcp.server.fastmcp import FastMCP
from cooperage_sdk import workspace, serve

mcp = FastMCP("my-server")

@mcp.tool()
def process_data(input_file: str, output_file: str) -> str:
    """Process a data file from the workspace and write results.

    Args:
        input_file: Path to input file in the workspace
        output_file: Path for output file in the workspace
    """
    data = workspace.path(input_file).read_text()
    workspace.path(output_file).write_text(data.upper())
    return f"Processed {input_file} → {output_file}"

serve(mcp)
```

That's the whole server. `workspace` gives you safe access to shared files. `serve(mcp)` starts everything up.

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

Add whatever your server needs (numpy, pandas, Pillow, etc.).

### 4. Hand off to your admin

Once your server is built and tested, give your admin the Docker image:

```bash
docker build -t my-server:latest .
```

They'll register it with Cooperage and make it available to users. See the main [README](../README.md) for registration and deployment details.

### Working with files

Use `workspace` to read and write shared files:

```python
# Read files
data = workspace.path("input.csv").read_text()

# Write files
workspace.path("results.json").write_text(json.dumps(results))

# Works with any library
image = Image.open(workspace.path("photo.png"))
df = pd.read_csv(workspace.path("data.csv"))
plt.savefig(workspace.path("chart.png"))

# Check what's in the workspace
workspace.exists("output.csv")
workspace.list()
workspace.list("reports")
```

### Adding documentation

Your server probably has domain knowledge the LLM doesn't have — procedures, format specs, workflow guides, etc. You can expose documentation so the LLM reads it on demand.

Add a `docs/` folder and call `register_docs(mcp)`:

```
my-server/
  server.py
  Dockerfile
  docs/
    quickstart.md
    output-format.md
    workflow-guide.md
```

```python
from cooperage_sdk import workspace, serve, register_docs

mcp = FastMCP("my-server")

# ... your tools ...

register_docs(mcp)
serve(mcp)
```

Make sure the `docs/` folder is copied in your Dockerfile:

```dockerfile
COPY docs/ docs/
```

The LLM sees a list of your docs with previews (the first line of each file) and reads the ones it needs. One topic per file works best.

### Testing locally

```bash
# Create a test workspace
mkdir -p /tmp/test-workspace
export COOPERAGE_WORKSPACE=/tmp/test-workspace

# Run your server
python server.py
```

---

## Without the SDK

If you prefer full control or don't want the SDK dependency, you can write servers using FastMCP directly. This section is for developers comfortable with Python web servers and MCP.

### Server

```python
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))

mcp = FastMCP("my-server", json_response=True, stateless_http=True)

@mcp.tool()
def my_tool(input_file: str) -> str:
    """Process a file from the workspace."""
    data = (WORKSPACE / input_file).read_text()
    (WORKSPACE / "output.txt").write_text(data.upper())
    return "Done"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)
```

Key requirements:
- `json_response=True, stateless_http=True` on FastMCP
- Listen on port 8000
- Read/write `/workspace` for shared files

### Documentation via MCP resources

Instead of `register_docs()`, you can register docs as MCP resources manually for more control over descriptions:

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

---

## Writing good tool descriptions

The LLM reads your tool descriptions to decide when and how to call them.

```python
# Bad — the LLM won't know what to pass or what happens
@mcp.tool()
def analyze(path: str) -> str:
    """Analyze a file."""
    ...

# Good — the LLM knows exactly what to expect
@mcp.tool()
def analyze_image(image_path: str) -> str:
    """Analyze an image from the workspace and save a JSON report.

    Computes per-channel color statistics (mean, std, min, max) and
    saves the report as <image_name>_analysis.json in the workspace.

    Args:
        image_path: Path to a PNG or JPEG image in the workspace
    """
    ...
```

## Multiple tools per server

Group related tools into one server:

```python
mcp = FastMCP("data-tools")

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

## Examples

| Server | What it does | Source |
|--------|-------------|--------|
| [image-analyzer](../example-servers/image-analyzer/) | Analyzes images using NumPy/PIL, outputs JSON stats | Good starting point |
| [synthetic-image-generator](../example-servers/synthetic-image-generator/) | Generates procedural imagery | Multiple tools + metadata |

## Checklist

- [ ] Tools have clear descriptions of what they do, what they expect, and what they write
- [ ] All shared data goes through the workspace
- [ ] Dockerfile builds and runs: `docker build -t name . && docker run -p 8000:8000 name`
