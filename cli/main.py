#!/usr/bin/env python
"""Interactive CLI for querying the RAG agent."""

import sys
from pathlib import Path

# Support direct execution while retaining the existing project layout.
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from src.agent.rag_agent import RAGAgent
from src.embeddings.embedding_generator import get_embeddings
from src.utils.helpers import setup_logging
from src.vectorstore.vector_db import VectorDatabase


console = Console()


def _print_startup_error(exc: Exception) -> None:
    console.print(
        Panel.fit(
            f"[bold red]Unable to start the RAG backend[/bold red]\n{exc}",
            border_style="red",
        )
    )


def run_cli() -> int:
    setup_logging("WARNING")
    console.print(
        Panel.fit(
            "[bold cyan]Agentic RAG Chatbot[/bold cyan]\n"
            "[dim]Ask a question, or use sources, clear, and quit.[/dim]",
            border_style="cyan",
        )
    )

    try:
        embeddings = get_embeddings()
        database = VectorDatabase(embeddings)
        chunk_count = database.count()
    except Exception as exc:
        _print_startup_error(exc)
        return 1

    if chunk_count == 0:
        console.print(
            "[yellow]Warning:[/yellow] No documents found. "
            "Run [bold]python ingest.py[/bold] first."
        )
    else:
        try:
            source_count = len(database.list_sources())
        except Exception as exc:
            _print_startup_error(exc)
            return 1
        console.print(
            f"[green]Loaded {chunk_count} chunks from "
            f"{source_count} document(s).[/green]"
        )

    agent = None
    while True:
        try:
            console.print()
            question = Prompt.ask("[bold green]You[/bold green]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            return 0

        question = question.strip()
        command = question.lower()
        if not question:
            continue
        if command in {"quit", "exit", "q"}:
            console.print("[dim]Goodbye![/dim]")
            return 0
        if command == "clear":
            if agent is not None:
                agent.clear_memory()
            console.print("[dim]Conversation memory cleared.[/dim]")
            continue
        if command == "sources":
            try:
                sources = database.list_sources()
                output = "\n".join(
                    f"  • {Path(source).name}"
                    for source in sources
                )
                console.print(output or "No sources ingested.")
            except Exception as exc:
                console.print(f"[red]Unable to list sources:[/red] {exc}")
            continue
        if chunk_count == 0:
            console.print(
                "[yellow]No documents are available yet. Run "
                "python ingest.py, then restart this CLI.[/yellow]"
            )
            continue

        if agent is None:
            try:
                with console.status("[bold blue]Loading language model…[/bold blue]"):
                    agent = RAGAgent(database)
            except Exception as exc:
                console.print(f"[red]Unable to initialize the model:[/red] {exc}")
                continue

        try:
            with console.status("[bold blue]Thinking…[/bold blue]"):
                result = agent.chat(question)
        except KeyboardInterrupt:
            console.print("\n[yellow]Request cancelled.[/yellow]")
            continue
        except Exception as exc:
            console.print(f"[red]Unable to answer the question:[/red] {exc}")
            continue

        console.print(Rule(style="dim"))
        console.print("[bold blue]Assistant:[/bold blue]")
        console.print(Markdown(result["answer"]))
        if result["sources"]:
            console.print("\n[dim]Sources:[/dim]")
            for source in result["sources"]:
                console.print(f"  [dim]• {Path(source).name}[/dim]")


if __name__ == "__main__":
    raise SystemExit(run_cli())
