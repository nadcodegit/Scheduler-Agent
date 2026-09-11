from __future__ import annotations

import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from PySide6.QtWidgets import QApplication

from desktop_app.main_window import MainWindow


def main() -> None:
    if load_dotenv is not None:
        load_dotenv()

    project_root = Path(__file__).resolve().parents[1]

    app = QApplication(sys.argv)
    window = MainWindow(project_root)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
