import asyncio
import logging

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="cooperage", help="Ephemeral MCP container orchestration.")
console = Console()

logging.basicConfig(level=logging.WARNING)


@app.command()
def register(
    name: str = typer.Option(..., help="Short name for this server"),
    image: str = typer.Option(..., help="Docker image (e.g. cooperage-analysis:latest)"),
    description: str = typer.Option("", help="Human-readable description"),
    port: int = typer.Option(8000, help="Port the MCP server listens on inside the container"),
    env: list[str] = typer.Option([], help="Environment variables in KEY=VALUE format"),
):
    """Register a Docker image as an MCP server."""
    from cooperage.core.models import ServerDef
    import cooperage.registry.registry as registry

    env_dict = {}
    for e in env:
        if "=" not in e:
            console.print(f"[red]Invalid env var (expected KEY=VALUE): {e}[/]")
            raise typer.Exit(1)
        k, v = e.split("=", 1)
        env_dict[k] = v

    server = ServerDef(name=name, image=image, description=description, port=port, env=env_dict)
    registry.register(server)
    console.print(f"[green]Registered[/] [bold]{name}[/] → {image}")


@app.command(name="list-servers")
def list_servers():
    """List registered MCP servers."""
    import cooperage.registry.registry as registry

    servers = registry.load()
    if not servers:
        console.print("[dim]No servers registered. Use `cooperage register` to add one.[/]")
        return

    table = Table(title="Registered Servers")
    table.add_column("Name", style="bold cyan")
    table.add_column("Image")
    table.add_column("Port")
    table.add_column("Description")
    for s in servers:
        table.add_row(s.name, s.image, str(s.port), s.description)
    console.print(table)


@app.command()
def deregister(name: str = typer.Argument(..., help="Server name to remove")):
    """Remove a server from the registry."""
    import cooperage.registry.registry as registry

    ok = registry.deregister(name)
    if ok:
        console.print(f"[yellow]Deregistered[/] {name}")
    else:
        console.print(f"[red]Server {name!r} not found in registry[/]")
        raise typer.Exit(1)


@app.command()
def sessions():
    """List active sessions."""
    import cooperage.session.manager as mgr

    active = mgr.list_sessions()
    if not active:
        console.print("[dim]No active sessions.[/]")
        return

    table = Table(title="Active Sessions")
    table.add_column("ID", style="bold cyan")
    table.add_column("Name")
    table.add_column("Volume")
    table.add_column("Expires At")
    table.add_column("Containers")
    for s in active:
        table.add_row(
            s.id[:16] + "...",
            s.name or "—",
            s.volume_name,
            s.expires_at.strftime("%H:%M:%S"),
            ", ".join(s.containers.keys()) or "—",
        )
    console.print(table)


@app.command()
def start(
    sse: bool = typer.Option(False, "--sse", help="Run as HTTP/SSE server instead of stdio"),
    host: str = typer.Option("0.0.0.0", help="Host to bind (SSE mode only)"),
    port: int = typer.Option(8080, help="Port to bind (SSE mode only)"),
    proxy: str = typer.Option(None, "--proxy", help="Forward stdio to a running SSE gateway URL"),
):
    """Start the Cooperage MCP gateway."""
    if proxy:
        asyncio.run(_run_proxy(proxy))
        return

    from cooperage.gateway.server import run_stdio, run_sse

    if sse:
        console.print(f"[green]Starting Cooperage gateway (SSE)[/] on {host}:{port}")
        asyncio.run(run_sse(host=host, port=port))
    else:
        asyncio.run(run_stdio())


async def _run_proxy(gateway_url: str) -> None:
    """Bridge stdio ↔ SSE gateway using MCP's Content-Length framing (LSP-style)."""
    import sys
    import json
    import httpx

    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    def read_message() -> str | None:
        """Read one Content-Length framed message from stdin."""
        content_length = None
        while True:
            header = sys.stdin.readline()
            if not header:
                return None
            header = header.strip()
            if not header:
                break  # blank line = end of headers
            if header.lower().startswith("content-length:"):
                content_length = int(header.split(":", 1)[1].strip())
        if content_length is None:
            return None
        return sys.stdin.read(content_length)

    def write_message(body: str) -> None:
        """Write one Content-Length framed message to stdout."""
        encoded = body.encode("utf-8")
        sys.stdout.write(f"Content-Length: {len(encoded)}\r\n\r\n")
        sys.stdout.write(body)
        sys.stdout.flush()

    async with httpx.AsyncClient(timeout=120) as client:
        loop = asyncio.get_event_loop()
        while True:
            body = await loop.run_in_executor(None, read_message)
            if body is None:
                break
            body = body.strip()
            if not body:
                continue
            try:
                msg = json.loads(body)
            except Exception:
                continue
            # Notifications have no "id" — fire and forget, no response written
            is_notification = "id" not in msg
            try:
                resp = await client.post(gateway_url, content=body, headers=headers)
                resp.raise_for_status()
                if not is_notification and resp.text.strip():
                    write_message(resp.text)
            except Exception as e:
                if not is_notification:
                    error = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": str(e)}}
                    write_message(json.dumps(error))


@app.command()
def ui(
    gateway: str = typer.Option("http://localhost:8080/mcp", help="Gateway URL"),
):
    """Open the Cooperage workspace UI in your browser."""
    import subprocess
    import sys
    from pathlib import Path

    ui_app = Path(__file__).parent.parent.parent / "ui" / "app.py"
    console.print(f"[green]Starting Cooperage UI[/] — gateway: {gateway}")
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", str(ui_app),
        "--", f"--gateway={gateway}",
    ])


@app.command(name="init-k8s")
def init_k8s():
    """Bootstrap the Cooperage namespace in Kubernetes."""
    from cooperage.core.config import settings
    try:
        from kubernetes import client, config as k8s_config
    except ImportError:
        console.print("[red]kubernetes package not installed. Run: uv add kubernetes[/]")
        raise typer.Exit(1)

    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()

    core = client.CoreV1Api()
    ns = settings.k8s_namespace

    try:
        core.read_namespace(name=ns)
        console.print(f"[dim]Namespace '{ns}' already exists.[/]")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            core.create_namespace(body=client.V1Namespace(
                metadata=client.V1ObjectMeta(name=ns, labels={"cooperage": "true"})
            ))
            console.print(f"[green]Created namespace '{ns}'.[/]")
        else:
            raise

    console.print(f"[green]Cooperage K8s namespace ready:[/] {ns}")
    console.print(f"  NodePort range: {settings.k8s_node_port_range_start}–{settings.k8s_node_port_range_end}")
    console.print(f"  Workspace host path: {settings.k8s_host_path_prefix}/<volume>")


if __name__ == "__main__":
    app()
