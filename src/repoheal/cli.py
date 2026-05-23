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




# ===========================================================================
# Phase 2 commands
# ===========================================================================


# --------------------------------------------------------------------------- #
# search — hybrid retrieval
# --------------------------------------------------------------------------- #


@app.command()
def search(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, dir_okay=True),
    query: str = typer.Option(..., "--query", "-q"),
    top_k: int = typer.Option(5, "--top-k"),
    token_budget: int | None = typer.Option(None, "--token-budget"),
) -> None:
    """Run the hybrid retriever against a path and print the top-k chunks."""
    configure_logging()
    service = AnalysisService()

    try:
        result = service.analyze(str(path), build_retrieval=True)
    except RepoHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if result.retrieval is None:
        typer.echo("error: retrieval index unavailable", err=True)
        raise typer.Exit(code=1)

    res = result.retrieval.search(query, top_k=top_k, token_budget=token_budget)
    if not res.chunks:
        _console.print(f"[yellow]no chunks for {query!r}[/yellow]")
        return

    for i, chunk in enumerate(res.chunks, 1):
        title = chunk.symbol_qname or f"{chunk.file_path}:lines:{chunk.start_line}-{chunk.end_line}"
        _console.print(f"[bold green]{i}. {title}[/bold green]  [dim]{chunk.file_path}[/dim]")
        first_line = chunk.text.splitlines()[0] if chunk.text else ""
        _console.print(f"   {first_line[:120]}")

    _console.print(
        f"[dim]Retrieved {len(res.chunks)} chunks in {res.timings.total_ms:.1f}ms"
        f" (cache_hit={res.cache_hit})[/dim]"
    )


# --------------------------------------------------------------------------- #
# agent — agent runner
# --------------------------------------------------------------------------- #


agent_app = typer.Typer(
    name="agent",
    help="Run agents.",
    no_args_is_help=True,
)
app.add_typer(agent_app, name="agent")


@agent_app.command("list")
def agent_list() -> None:
    """List registered agents."""
    from .agents import default_registry

    for name in sorted(default_registry().names()):
        typer.echo(name)


@agent_app.command("run")
def agent_run(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, dir_okay=True),
    agent: str = typer.Option(..., "--agent", "-a"),
    inputs_json: str = typer.Option("{}", "--inputs", help="JSON-encoded inputs dict."),
    timeout: float = typer.Option(60.0, "--timeout"),
    no_retrieval: bool = typer.Option(False, "--no-retrieval", help="Skip building the retrieval index."),
) -> None:
    """Run a single agent against a path."""
    import asyncio

    from .agents import AgentRunner, default_registry

    configure_logging()
    try:
        inputs = json.loads(inputs_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"error: invalid --inputs JSON: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    registry = default_registry()
    if not registry.has(agent):
        typer.echo(
            f"error: unknown agent {agent!r} (registered: {sorted(registry.names())})",
            err=True,
        )
        raise typer.Exit(code=2)

    service = AnalysisService()
    try:
        result = service.analyze(str(path), build_retrieval=not no_retrieval)
    except RepoHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    runner = AgentRunner(registry.create(agent), max_attempts=2, timeout_seconds=timeout)
    state = {
        "repo": result.repository,
        "graph": result.graph,
        "retrieval": result.retrieval,
        "inputs": inputs,
    }
    agent_result = asyncio.run(runner.run(state))

    typer.echo(json.dumps(agent_result.model_dump(mode="json"), indent=2, default=str))


# --------------------------------------------------------------------------- #
# trace — parse + correlate a traceback file
# --------------------------------------------------------------------------- #


@app.command()
def trace(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, dir_okay=True),
    traceback_file: Path = typer.Option(..., "--traceback", exists=True, file_okay=True, dir_okay=False),
) -> None:
    """Parse a traceback file and correlate every frame with the repo's graph."""
    from .runtime import TracebackCorrelator, TracebackParser

    configure_logging()
    text = traceback_file.read_text(encoding="utf-8", errors="replace")
    parsed = TracebackParser().parse(text)

    if not parsed.frames:
        typer.echo("no frames found in input")
        raise typer.Exit(code=1)

    service = AnalysisService()
    try:
        result = service.analyze(str(path))
    except RepoHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    correlator = TracebackCorrelator(result.repository, result.graph)
    correlated = correlator.correlate(parsed)

    if parsed.exception_type:
        msg = f": {parsed.exception_message}" if parsed.exception_message else ""
        _console.print(f"[bold red]{parsed.exception_type}{msg}[/bold red]")

    table = Table(show_header=True)
    table.add_column("#")
    table.add_column("file")
    table.add_column("line")
    table.add_column("function")
    table.add_column("qname")
    table.add_column("conf")
    for i, cf in enumerate(correlated.frames, 1):
        table.add_row(
            str(i),
            str(cf.file_in_repo or cf.frame.file),
            str(cf.frame.line),
            cf.frame.function,
            cf.qualified_name or "-",
            f"{cf.confidence:.2f}",
        )
    _console.print(table)


# --------------------------------------------------------------------------- #
# issue correlate — local issue file → candidate files / frames
# --------------------------------------------------------------------------- #


issue_app = typer.Typer(
    name="issue",
    help="Issue intelligence commands.",
    no_args_is_help=True,
)
app.add_typer(issue_app, name="issue")


@issue_app.command("correlate")
def issue_correlate(
    path: Path = typer.Option(..., "--path", "-p", exists=True, file_okay=False, dir_okay=True),
    issue_file: Path = typer.Option(..., "--issue", exists=True, file_okay=True, dir_okay=False),
) -> None:
    """Correlate a local issue (JSON file with title + body + comments)
    against a repository's graph + retrieval index."""
    from .core.models import Issue
    from .issues import IssueCorrelator

    configure_logging()

    try:
        raw = json.loads(issue_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"error: cannot read issue file: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    issue = Issue(
        source="local",
        number=int(raw.get("number", 0)),
        title=str(raw.get("title", "")),
        body=str(raw.get("body", "")),
        labels=tuple(raw.get("labels", ())),
        url=raw.get("url"),
        metadata={
            "comments": [
                {"body": c} if isinstance(c, str) else c
                for c in raw.get("comments", [])
                if c
            ],
        },
    )

    service = AnalysisService()
    try:
        result = service.analyze(str(path), build_retrieval=True)
    except RepoHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    correlator = IssueCorrelator(
        result.repository,
        result.graph,
        retrieval=result.retrieval,
    )
    out = correlator.correlate(issue)

    if out.primary_anchor_node:
        _console.print(f"[bold]Primary anchor:[/bold] {out.primary_anchor_node}")

    if out.correlated_tracebacks:
        _console.print(f"[bold]Tracebacks:[/bold] {len(out.correlated_tracebacks)}")
        for tb in out.correlated_tracebacks:
            for frame in tb.frames:
                qn = frame.qualified_name or "-"
                _console.print(
                    f"  - {frame.frame.file}:{frame.frame.line} in {frame.frame.function} -> {qn}"
                )

    if out.candidate_files:
        _console.print(f"[bold]Candidate files ({len(out.candidate_files)}):[/bold]")
        for f in out.candidate_files[:10]:
            _console.print(f"  - {f}")
