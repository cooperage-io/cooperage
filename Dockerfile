FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml .
COPY cooperage/ cooperage/

RUN uv pip install --system --no-cache .

CMD ["cooperage", "start", "--sse"]
