"""Keep QtQml's linked runtime without bundling every optional QML module.

Qt WebEngine Widgets links against QtQml, but Tournament Control does not load
QML files. The stock PyInstaller hook scans the complete PySide6 Addons QML
tree (3D, charts, multimedia, virtual keyboard, and more), adding thousands of
unused files to the distribution.
"""

from PyInstaller.utils.hooks.qt import add_qt6_dependencies


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
