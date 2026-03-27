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

mcp = FastMCP("image-analyzer", json_response=True, stateless_http=True)


@mcp.tool()
def analyze_scenes(image_paths: list[str]) -> str:
    """Compute per-channel color statistics on multiple images in /workspace in one call.
    image_paths is a list of paths relative to /workspace (e.g. ['scenes/terrain.png', 'scenes/urban.png']).
    Use this instead of calling analyze_scene repeatedly."""
    results = {}
    for image_path in image_paths:
        img_path = WORKSPACE / image_path
        if not img_path.exists():
            results[image_path] = f"not found in /workspace"
            continue
        pixels = np.array(Image.open(img_path).convert("RGB"))
        results[image_path] = {
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
    return json.dumps(results, indent=2)


@mcp.tool()
def analyze_scene(image_path: str = "scene.png") -> str:
    """Compute per-channel color statistics on an image in /workspace.
    image_path is relative to /workspace (e.g. 'scenes/terrain.png').
    Run the simulator's generate_scene tool first to produce the image."""
    img_path = WORKSPACE / image_path
    if not img_path.exists():
        return f"{image_path} not found in /workspace. Run generate_scene first."

    pixels = np.array(Image.open(img_path).convert("RGB"))
    result = {
        "image_path": image_path,
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
