import asyncio
import logging
import os

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="cooperage", help="Where AI tools work together.")
console = Console()

logging.basicConfig(level=logging.WARNING)


@app.command()
def register(
    name: str = typer.Option(None, help="Short name for this server"),
    image: str = typer.Option(None, help="Docker image (e.g. cooperage-image-analyzer:latest)"),
    description: str = typer.Option("", help="Human-readable description"),
    port: int = typer.Option(8000, help="Port the MCP server listens on inside the container"),
    env: list[str] = typer.Option([], help="Environment variables in KEY=VALUE format"),
    from_file: str = typer.Option(None, "--from", help="YAML/JSON adapter config file (wraps REST APIs, LangChain tools, or Python functions)"),
):
    """Register a Docker image as an MCP server, or wrap an external tool via --from."""
    from cooperage.core.models import ServerDef
    import cooperage.registry.registry as registry

    env_dict = {}
    for e in env:
        if "=" not in e:
            console.print(f"[red]Invalid env var (expected KEY=VALUE): {e}[/]")
            raise typer.Exit(1)
        k, v = e.split("=", 1)
        env_dict[k] = v

    if from_file:
        from cooperage.adapter.config import AdapterConfig
        from pathlib import Path
        import json

        path = Path(from_file)
        if not path.exists():
            console.print(f"[red]Config file not found: {from_file}[/]")
            raise typer.Exit(1)

        raw = path.read_text()
        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(raw)
            except ImportError:
                console.print("[red]pyyaml required for YAML configs. Install with: uv add pyyaml[/]")
                raise typer.Exit(1)
        else:
            data = json.loads(raw)

        config = AdapterConfig(**data)
        adapter_env = {"COOPERAGE_ADAPTER_CONFIG": config.model_dump_json()}
        adapter_env.update(env_dict)

        server = ServerDef(
            name=config.name,
            image="cooperage-adapter:latest",
            description=config.description or f"Adapter: {config.type.value}",
            port=8000,
            env=adapter_env,
        )
        registry.register(server)
        console.print(f"[green]Registered adapter[/] [bold]{config.name}[/] (type: {config.type.value})")
        return

    if not name or not image:
        console.print("[red]--name and --image are required (or use --from for adapter configs)[/]")
        raise typer.Exit(1)

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
    proxy: str = typer.Option(None, "--proxy", help="Forward all MCP traffic to a remote gateway URL"),
    api_key: str = typer.Option(None, "--api-key", help="API key for authenticating with a remote gateway (used with --proxy). Also reads COOPERAGE_API_KEY env var."),
):
    """Start the Cooperage MCP gateway."""
    from cooperage.gateway.server import run_proxy, run_stdio, run_sse

    resolved_key = api_key or os.environ.get("COOPERAGE_API_KEY")

    if proxy:
        asyncio.run(run_proxy(proxy, api_key=resolved_key))
    elif sse:
        console.print(f"[green]Starting Cooperage gateway (SSE)[/] on {host}:{port}")
        asyncio.run(run_sse(host=host, port=port))
    else:
        asyncio.run(run_stdio())


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


@app.command()
def clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Stop all cooperage containers and clear session state. Use before a demo."""
    import subprocess
    from cooperage.core.config import settings

    if not yes:
        typer.confirm("Stop all cooperage containers and clear sessions?", abort=True)

    # Stop all cooperage containers
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    containers = [n for n in result.stdout.splitlines() if n.startswith("cooperage-")]
    if containers:
        subprocess.run(["docker", "stop"] + containers, capture_output=True)
        console.print(f"[yellow]Stopped[/] {len(containers)} container(s): {', '.join(containers)}")
    else:
        console.print("[dim]No running cooperage containers.[/]")

    # Clear sessions file
    path = settings.sessions_path
    if path.exists():
        path.unlink()
        console.print(f"[yellow]Cleared[/] {path}")
    else:
        console.print("[dim]No sessions file to clear.[/]")

    console.print("[green]Ready.[/] Restart the gateway to begin a fresh session.")


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
