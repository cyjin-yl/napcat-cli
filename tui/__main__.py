"""napcat TUI entry point."""
from __future__ import annotations

from .app import NapCatApp


def main() -> None:
    app = NapCatApp()
    app.run()


if __name__ == "__main__":
    main()
