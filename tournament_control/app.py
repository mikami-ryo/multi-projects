from __future__ import annotations

import os
import sys


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtWidgets import QApplication

    from .paths import AppPaths
    from .ui import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Tournament Control")
    app.setOrganizationName("Tournament Control")
    window = MainWindow(AppPaths.create())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
