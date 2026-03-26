import sys
from pathlib import Path

# Make example server modules importable as `example_servers.<name>.server`
# despite example-servers/ having a hyphen in the directory name.
_examples_dir = Path(__file__).parent.parent / "example-servers"
for _server_dir in _examples_dir.iterdir():
    if _server_dir.is_dir():
        sys.path.insert(0, str(_server_dir))
