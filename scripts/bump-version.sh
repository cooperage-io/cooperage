#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <new-version>"
    echo "Example: $0 0.2.0"
    exit 1
fi

VERSION="$1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Validate format
if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "Error: version must be in X.Y.Z format (got '$VERSION')"
    exit 1
fi

# Check we're on main
BRANCH="$(git -C "$ROOT" branch --show-current)"
if [ "$BRANCH" != "main" ]; then
    echo "Error: must be on main branch (currently on '$BRANCH')"
    exit 1
fi

# Check for clean working tree
if ! git -C "$ROOT" diff --quiet || ! git -C "$ROOT" diff --cached --quiet; then
    echo "Error: working tree is not clean — commit or stash changes first"
    exit 1
fi

# Check tag doesn't already exist
if git -C "$ROOT" tag -l "v$VERSION" | grep -q .; then
    echo "Error: tag v$VERSION already exists"
    exit 1
fi

# Update pyproject.toml
sed -i "s/^version = \".*\"/version = \"$VERSION\"/" "$ROOT/pyproject.toml"

# Update Chart.yaml
sed -i "s/^version: .*/version: $VERSION/" "$ROOT/chart/Chart.yaml"
sed -i "s/^appVersion: .*/appVersion: \"$VERSION\"/" "$ROOT/chart/Chart.yaml"

echo "Updated pyproject.toml and chart/Chart.yaml to $VERSION"

# Commit and tag
git -C "$ROOT" add pyproject.toml chart/Chart.yaml
git -C "$ROOT" commit -m "Bump version to $VERSION"
git -C "$ROOT" tag "v$VERSION"

echo ""
echo "Done. To publish:"
echo "  git push origin main --tags"
