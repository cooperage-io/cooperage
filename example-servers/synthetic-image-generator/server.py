"""
Cooperage Example: Imagery Simulator MCP Server

Generates synthetic satellite imagery and writes it to /workspace.
A second container (e.g. cooperage-image-analyzer) can then read and process the
same /workspace volume within the same Cooperage session.

Tools:
  generate_scene   — render synthetic satellite imagery (terrain/urban/coastal)
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import uvicorn
from PIL import Image
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("synthetic-image-generator", json_response=True, stateless_http=True)


# ── Scene generators ──────────────────────────────────────────────────────────

def _terrain_scene(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    """Rolling hills with vegetation — greens and browns."""
    base = rng.random((height, width))
    from numpy.lib.stride_tricks import sliding_window_view
    k = 15
    padded = np.pad(base, k // 2, mode="edge")
    smoothed = sliding_window_view(padded, (k, k)).mean(axis=(-2, -1))

    r = (smoothed * 80 + rng.random((height, width)) * 20 + 40).clip(0, 255)
    g = (smoothed * 120 + rng.random((height, width)) * 25 + 60).clip(0, 255)
    b = (smoothed * 40 + rng.random((height, width)) * 15 + 20).clip(0, 255)
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def _urban_scene(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    """City grid — grey blocks with road network."""
    img = np.full((height, width, 3), 80, dtype=np.uint8)

    spacing = max(width // 12, 20)
    road_color = np.array([50, 50, 50], dtype=np.uint8)
    for x in range(0, width, spacing):
        img[:, max(0, x - 1):x + 2] = road_color
    for y in range(0, height, spacing):
        img[max(0, y - 1):y + 2, :] = road_color

    for _ in range(60):
        bx = int(rng.integers(0, width - 30))
        by = int(rng.integers(0, height - 30))
        bw = int(rng.integers(10, 30))
        bh = int(rng.integers(10, 30))
        shade = int(rng.integers(100, 200))
        img[by:by + bh, bx:bx + bw] = [shade, shade - 5, shade - 10]

    noise = rng.integers(-10, 10, (height, width, 3))
    return (img.astype(np.int16) + noise).clip(0, 255).astype(np.uint8)


def _coastal_scene(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    """Ocean meets shoreline — blues fading to sandy beige."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    shore_y = int(height * (0.4 + rng.random() * 0.2))

    for y in range(shore_y):
        depth = 1 - y / shore_y
        r = int(20 + depth * 30 + rng.random() * 10)
        g = int(60 + depth * 80 + rng.random() * 15)
        b = int(150 + depth * 80 + rng.random() * 20)
        img[y, :] = np.clip([r, g, b], 0, 255)

    for y in range(shore_y, height):
        t = (y - shore_y) / max(height - shore_y, 1)
        r = int(180 + t * 50 + rng.random() * 20)
        g = int(160 + t * 40 + rng.random() * 20)
        b = int(100 + t * 20 + rng.random() * 15)
        img[y, :] = np.clip([r, g, b], 0, 255)

    noise = rng.integers(-8, 8, (height, width, 3))
    return (img.astype(np.int16) + noise).clip(0, 255).astype(np.uint8)


_GENERATORS = {
    "terrain": _terrain_scene,
    "urban": _urban_scene,
    "coastal": _coastal_scene,
}


# ── Internal helpers (used by MCP tool and tests) ─────────────────────────────

def _generate_scene(
    scene_type: str,
    width: int = 256,
    height: int = 256,
    seed: int | None = None,
    output_path: str = "scene.png",
) -> dict:
    """Generate a scene and write files; return the metadata dict."""
    if scene_type not in _GENERATORS:
        raise ValueError(f"Unknown scene_type: {scene_type!r}. Choose from {list(_GENERATORS)}")

    rng = np.random.default_rng(seed)
    pixels = _GENERATORS[scene_type](width, height, rng)

    img_path = WORKSPACE / output_path
    img_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, mode="RGB").save(img_path)

    meta_path = img_path.with_suffix(".json")
    stats = {
        "mean_r": round(float(pixels[:, :, 0].mean()), 2),
        "mean_g": round(float(pixels[:, :, 1].mean()), 2),
        "mean_b": round(float(pixels[:, :, 2].mean()), 2),
        "std":    round(float(pixels.std()), 2),
        "min":    int(pixels.min()),
        "max":    int(pixels.max()),
    }

    meta = {
        "scene_type": scene_type,
        "width": width,
        "height": height,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": {"image": output_path, "metadata": str(meta_path.relative_to(WORKSPACE))},
        "stats": stats,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Async tool dispatcher — mirrors FastMCP routing for direct testing."""
    if name == "generate_scene":
        meta = _generate_scene(
            scene_type=arguments.get("scene_type", "terrain"),
            width=int(arguments.get("width", 512)),
            height=int(arguments.get("height", 512)),
            seed=arguments.get("seed"),
            output_path=arguments.get("output_path", "scene.png"),
        )
        return [TextContent(type="text", text=json.dumps(meta, indent=2))]

    if name == "list_workspace":
        files = sorted(p.name for p in WORKSPACE.iterdir() if p.is_file())
        text = "\n".join(files) if files else "(empty)"
        return [TextContent(type="text", text=text)]

    return [TextContent(type="text", text=f"Unknown tool: {name!r}")]


# ── MCP tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def generate_all_scenes(
    width: int = 256,
    height: int = 256,
    seed: int | None = None,
    output_dir: str = "scenes",
) -> str:
    """Generate all three scene types (terrain, urban, coastal) in one call.
    Saves each to {output_dir}/{scene_type}.png in /workspace.
    Use this instead of calling generate_scene three times."""
    results = {}
    for scene_type in ("terrain", "urban", "coastal"):
        output_path = f"{output_dir}/{scene_type}.png"
        results[scene_type] = _generate_scene(scene_type, width, height, seed, output_path)
    return json.dumps(results, indent=2)


@mcp.tool()
def generate_scene(
    scene_type: Literal["terrain", "urban", "coastal"],
    width: int = 256,
    height: int = 256,
    seed: int | None = None,
    output_path: str = "scene.png",
) -> str:
    """Generate synthetic satellite imagery and save it to /workspace.
    Returns image stats and file paths.
    Use output_path to save to a specific location (e.g. 'scenes/terrain.png')
    so multiple scenes can coexist without overwriting each other.
    Other Cooperage servers in the same session can read the output from /workspace."""
    meta = _generate_scene(scene_type, width, height, seed, output_path)
    return json.dumps(meta, indent=2)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)
