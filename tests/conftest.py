import sys
from pathlib import Path

# Make example server modules importable as `example_servers.<name>.server`
# despite example-servers/ having a hyphen in the directory name.
# Sort alphabetically so each dir is inserted at sys.path[0] in order —
# the last insertion wins, so 'simulator' (s > a) ends up at position 0
# and bare `import server` resolves to the simulator server.
_examples_dir = Path(__file__).parent.parent / "example-servers"
for _server_dir in sorted(_examples_dir.iterdir()):
    if _server_dir.is_dir():
        sys.path.insert(0, str(_server_dir))
