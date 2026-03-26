FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python deps via uv
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY server.py .

CMD ["python", "server.py"]
