"""Entry point for `python -m sheldrake` and the `sheldrake` CLI command."""

from __future__ import annotations

import typer

from sheldrake.config import DEFAULT_MODEL

app = typer.Typer(add_completion=False)


@app.command()
def main(
    model: str = typer.Option(DEFAULT_MODEL, help="Anthropic model ID"),
    debug: bool = typer.Option(False, help="Show debug panel with raw token stream"),
) -> None:
    """Sheldrake — Where AI learns to use the backspace key."""
    from sheldrake.app import SheldrakeApp

    tui = SheldrakeApp(model=model, debug=debug)
    tui.run()


if __name__ == "__main__":
    app()
