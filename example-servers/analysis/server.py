"""
Cooperage Example: Analysis MCP Server

Demonstrates domain-specific compute using a shared /workspace volume.
Use cooperage_run_script for general Python execution — this server shows
how to build a specialized server with custom tools on top of workspace data.

Tools:
  analyze_scene  — compute statistics on a scene.png written by the simulator
"""

import json
import os
from pathlib import Path

import numpy as np
import uvicorn
from PIL import Image
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("cooperage-analysis", json_response=True, stateless_http=True)


@mcp.tool()
def analyze_scene() -> str:
    """Compute per-channel statistics on scene.png in /workspace.
    Run the simulator's generate_scene tool first to produce the image."""
    img_path = WORKSPACE / "scene.png"
    if not img_path.exists():
        return "scene.png not found in /workspace. Run generate_scene first."

    pixels = np.array(Image.open(img_path).convert("RGB"))
    result = {
        "shape": list(pixels.shape),
        "channels": {
            "red":   {"mean": round(float(pixels[:, :, 0].mean()), 2), "std": round(float(pixels[:, :, 0].std()), 2)},
            "green": {"mean": round(float(pixels[:, :, 1].mean()), 2), "std": round(float(pixels[:, :, 1].std()), 2)},
            "blue":  {"mean": round(float(pixels[:, :, 2].mean()), 2), "std": round(float(pixels[:, :, 2].std()), 2)},
        },
        "overall": {
            "min": int(pixels.min()),
            "max": int(pixels.max()),
            "std": round(float(pixels.std()), 2),
        },
    }
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)
