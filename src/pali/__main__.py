"""Entry point for `python -m pali` and the `pali` CLI command."""

from __future__ import annotations

import typer

from pali.config import DEFAULT_MODEL

app = typer.Typer(add_completion=False)


@app.command()
def main(
    model: str = typer.Option(DEFAULT_MODEL, help="Anthropic model ID"),
    debug: bool = typer.Option(False, help="Show debug panel with raw token stream"),
) -> None:
    """Pali — Where AI learns to use the backspace key."""
    from pali.app import PalimpsestApp

    tui = PalimpsestApp(model=model, debug=debug)
    tui.run()


if __name__ == "__main__":
    app()
