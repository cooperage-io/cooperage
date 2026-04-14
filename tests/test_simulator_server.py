"""
Simulator server tests — filesystem interactions use tmp_path, no Docker needed.
"""
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

_SIM_PATH = Path(__file__).parent.parent / "example-servers" / "synthetic-image-generator" / "server.py"


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    return tmp_path


def _import_srv():
    spec = importlib.util.spec_from_file_location("simulator_server", _SIM_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── generate_scene ────────────────────────────────────────────────────────────

def test_generate_scene_creates_image_file(tmp_path):
    srv = _import_srv()
    srv._generate_scene("terrain", 64, 64, seed=42)
    assert (tmp_path / "scene.png").exists()


def test_generate_scene_creates_metadata_file(tmp_path):
    srv = _import_srv()
    srv._generate_scene("urban", 64, 64, seed=1)
    meta_path = tmp_path / "scene.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["scene_type"] == "urban"
    assert meta["width"] == 64
    assert meta["height"] == 64
    assert "generated_at" in meta
    assert "stats" in meta


def test_generate_scene_returns_expected_keys(tmp_path):
    srv = _import_srv()
    result = srv._generate_scene("coastal", 32, 32, seed=7)
    for key in ("scene_type", "width", "height", "generated_at", "stats", "files"):
        assert key in result


def test_generate_scene_stats_are_valid(tmp_path):
    srv = _import_srv()
    result = srv._generate_scene("terrain", 64, 64, seed=99)
    stats = result["stats"]
    assert 0 <= stats["mean_r"] <= 255
    assert 0 <= stats["mean_g"] <= 255
    assert 0 <= stats["mean_b"] <= 255
    assert stats["min"] >= 0
    assert stats["max"] <= 255


def test_generate_scene_reproducible_with_seed(tmp_path):
    srv = _import_srv()
    r1 = srv._generate_scene("terrain", 32, 32, seed=42)
    r2 = srv._generate_scene("terrain", 32, 32, seed=42)
    assert r1["stats"] == r2["stats"]


def test_generate_scene_different_with_different_seed(tmp_path):
    srv = _import_srv()
    r1 = srv._generate_scene("terrain", 64, 64, seed=1)
    r2 = srv._generate_scene("terrain", 64, 64, seed=2)
    assert r1["stats"] != r2["stats"]


@pytest.mark.parametrize("scene_type", ["terrain", "urban", "coastal"])
def test_all_scene_types_produce_valid_output(tmp_path, scene_type):
    srv = _import_srv()
    result = srv._generate_scene(scene_type, 64, 64, seed=0)
    assert result["scene_type"] == scene_type
    assert (tmp_path / "scene.png").exists()


def test_generate_scene_invalid_type_raises(tmp_path):
    srv = _import_srv()
    with pytest.raises(ValueError, match="Unknown scene_type"):
        srv._generate_scene("desert", 64, 64, seed=0)


def test_generate_scene_image_correct_size(tmp_path):
    from PIL import Image
    srv = _import_srv()
    srv._generate_scene("urban", 128, 96, seed=5)
    img = Image.open(tmp_path / "scene.png")
    assert img.size == (128, 96)
