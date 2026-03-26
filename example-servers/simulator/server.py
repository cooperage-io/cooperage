"""
Cooperage Example: Imagery Simulator MCP Server

Generates synthetic satellite imagery and writes it to /workspace.
A second container (e.g. cooperage-analysis) can then read and process the
same /workspace volume within the same Cooperage session.

Tools:
  generate_scene   — render synthetic satellite imagery (terrain/urban/coastal)
  list_workspace   — list files currently in /workspace
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp import types
import uvicorn

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

app = Server("cooperage-simulator")

# ── Scene generators ──────────────────────────────────────────────────────────

def _terrain_scene(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    """Rolling hills with vegetation — greens and browns."""
    base = rng.random((height, width))
    # Smooth with a simple box filter approximation
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

    # Road grid
    spacing = max(width // 12, 20)
    road_color = np.array([50, 50, 50], dtype=np.uint8)
    for x in range(0, width, spacing):
        img[:, max(0, x - 1):x + 2] = road_color
    for y in range(0, height, spacing):
        img[max(0, y - 1):y + 2, :] = road_color

    # City blocks (random grey rectangles)
    for _ in range(60):
        bx = int(rng.integers(0, width - 30))
        by = int(rng.integers(0, height - 30))
        bw = int(rng.integers(10, 30))
        bh = int(rng.integers(10, 30))
        shade = int(rng.integers(100, 200))
        img[by:by + bh, bx:bx + bw] = [shade, shade - 5, shade - 10]

    # Noise
    noise = rng.integers(-10, 10, (height, width, 3))
    return (img.astype(np.int16) + noise).clip(0, 255).astype(np.uint8)


def _coastal_scene(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    """Ocean meets shoreline — blues fading to sandy beige."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    shore_y = int(height * (0.4 + rng.random() * 0.2))

    # Water
    for y in range(shore_y):
        depth = 1 - y / shore_y
        r = int(20 + depth * 30 + rng.random() * 10)
        g = int(60 + depth * 80 + rng.random() * 15)
        b = int(150 + depth * 80 + rng.random() * 20)
        img[y, :] = np.clip([r, g, b], 0, 255)

    # Sand / land
    for y in range(shore_y, height):
        t = (y - shore_y) / max(height - shore_y, 1)
        r = int(180 + t * 50 + rng.random() * 20)
        g = int(160 + t * 40 + rng.random() * 20)
        b = int(100 + t * 20 + rng.random() * 15)
        img[y, :] = np.clip([r, g, b], 0, 255)

    # Noise
    noise = rng.integers(-8, 8, (height, width, 3))
    return (img.astype(np.int16) + noise).clip(0, 255).astype(np.uint8)


_GENERATORS = {
    "terrain": _terrain_scene,
    "urban": _urban_scene,
    "coastal": _coastal_scene,
}

# ── MCP tools ─────────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="generate_scene",
            description=(
                "Generate synthetic satellite imagery and save it to /workspace. "
                "Returns image stats and file paths. "
                "Other Cooperage servers in the same session can read the output from /workspace."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "scene_type": {
                        "type": "string",
                        "enum": ["terrain", "urban", "coastal"],
                        "description": "Type of scene to generate",
                    },
                    "width": {"type": "integer", "default": 512, "description": "Image width in pixels"},
                    "height": {"type": "integer", "default": 512, "description": "Image height in pixels"},
                    "seed": {"type": "integer", "description": "Optional random seed for reproducibility"},
                },
                "required": ["scene_type"],
            },
        ),
        types.Tool(
            name="list_workspace",
            description="List all files currently in /workspace.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "generate_scene":
        result = _generate_scene(
            scene_type=arguments.get("scene_type", "terrain"),
            width=int(arguments.get("width", 512)),
            height=int(arguments.get("height", 512)),
            seed=arguments.get("seed"),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "list_workspace":
        files = sorted(
            str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*") if p.is_file()
        )
        return [types.TextContent(type="text", text="\n".join(files) if files else "(empty)")]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


def _generate_scene(
    scene_type: str,
    width: int,
    height: int,
    seed: int | None,
) -> dict:
    if scene_type not in _GENERATORS:
        raise ValueError(f"Unknown scene_type {scene_type!r}. Choose from: {list(_GENERATORS)}")

    rng = np.random.default_rng(seed)
    pixels = _GENERATORS[scene_type](width, height, rng)

    img_path = WORKSPACE / "scene.png"
    Image.fromarray(pixels, mode="RGB").save(img_path)

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
        "files": {
            "image": "scene.png",
            "metadata": "scene_meta.json",
        },
        "stats": stats,
    }
    (WORKSPACE / "scene_meta.json").write_text(json.dumps(meta, indent=2))

    return meta


# ── ASGI app ──────────────────────────────────────────────────────────────────

session_manager = StreamableHTTPSessionManager(
    app=app,
    json_response=True,
    stateless=True,
)


class CooperageASGIApp:
    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
        else:
            await session_manager.handle_request(scope, receive, send)

    async def _lifespan(self, receive, send):
        async with session_manager.run():
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})


asgi_app = CooperageASGIApp()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(asgi_app, host="0.0.0.0", port=port)
