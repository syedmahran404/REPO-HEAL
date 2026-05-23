"""Typer-based command-line interface.

Mirrors the HTTP API surface for local use:

* ``repoheal ingest --path PATH`` — print ecosystem + file count.
* ``repoheal graph build --path PATH [--out FILE]`` — dump the graph.
* ``repoheal scan --path PATH [--rule RULE]`` — run detection rules.
* ``repoheal version`` — print version.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .analysis import AnalysisService
from .exceptions import RepoHealError
from .logging import configure_logging

app = typer.Typer(
    name="repoheal",
    help="Autonomous repository analysis & self-healing engine.",
    no_args_is_help=True,
    add_completion=False,
)
graph_app = typer.Typer(name="graph", help="Knowledge graph operations.", no_args_is_help=True)
app.add_typer(graph_app, name="graph")

_console = Console()


# --------------------------------------------------------------------------- #
# version
# --------------------------------------------------------------------------- #


@app.command()
def version() -> None:
    """Print the version and exit."""
    typer.echo(__version__)


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #


@app.command()
def ingest(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, dir_okay=True),
    branch: str | None = typer.Option(None, "--branch"),
) -> None:
    """Ingest a local repository and print its summary."""
    configure_logging()
    service = AnalysisService()

    try:
        result = service.analyze(str(path), branch=branch)
    except RepoHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    repo = result.repository
    table = Table(title=f"Ingested {repo.name}", show_header=True)
    table.add_column("field")
    table.add_column("value")
    table.add_row("root", str(repo.root))
    table.add_row("commit", repo.commit or "-")
    table.add_row("languages", ", ".join(l.value for l in repo.ecosystem.languages) or "-")
    table.add_row("build systems", ", ".join(b.value for b in repo.ecosystem.build_systems) or "-")
    table.add_row("frameworks", ", ".join(repo.ecosystem.frameworks) or "-")
    table.add_row("test frameworks", ", ".join(repo.ecosystem.test_frameworks) or "-")
    table.add_row("file count", str(repo.file_count))
    table.add_row("graph nodes", str(result.graph.node_count()))
    table.add_row("graph edges", str(result.graph.edge_count()))
    _console.print(table)


# --------------------------------------------------------------------------- #
# graph subcommands
# --------------------------------------------------------------------------- #


@graph_app.command("build")
def graph_build(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, dir_okay=True),
    out: Path | None = typer.Option(None, "--out", "-o"),
) -> None:
    """Build the knowledge graph and emit it as JSON."""
    configure_logging()
    service = AnalysisService()

    try:
        result = service.analyze(str(path))
    except RepoHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = {
        "stats": result.graph_stats(),
        "graph": result.graph.to_dict(),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if out is None:
        sys.stdout.write(text)
        sys.stdout.write("\n")
    else:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out} ({result.graph.node_count()} nodes, {result.graph.edge_count()} edges)")


@graph_app.command("cycles")
def graph_cycles(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, dir_okay=True),
    kind: str = typer.Option("imports", "--kind"),
) -> None:
    """Print graph cycles (default: import cycles)."""
    configure_logging()
    service = AnalysisService()
    result = service.analyze(str(path))
    cycles = result.graph.find_cycles(kind=kind)
    if not cycles:
        _console.print(f"[green]no {kind} cycles[/green]")
        return
    for i, cycle in enumerate(cycles, 1):
        _console.print(f"[red]cycle {i} ({len(cycle)} nodes):[/red]")
        for node in cycle:
            _console.print(f"  - {node}")


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #


@app.command()
def scan(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, dir_okay=True),
    rule: list[str] | None = typer.Option(None, "--rule", help="Repeatable rule id."),
) -> None:
    """Run detection rules and print findings."""
    configure_logging()
    service = AnalysisService()
    try:
        result = service.analyze(str(path), rule_ids=rule)
    except RepoHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not result.findings:
        _console.print("[green]no findings[/green]")
        return

    for f in result.findings:
        _console.print(f"[bold yellow]{f.rule_id}[/bold yellow] [{f.severity.value}] {f.title}")
        _console.print(f.description)
        if f.metadata:
            _console.print(f"  metadata: {f.metadata}")
        _console.print("")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
