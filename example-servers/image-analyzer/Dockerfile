FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY server.py .

# /workspace is where the session volume is mounted
RUN mkdir -p /workspace

EXPOSE 8000

CMD ["python", "server.py"]
