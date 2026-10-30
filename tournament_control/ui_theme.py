APP_STYLE = """
QWidget { color: #172033; }
QMainWindow { background: #f4f6f9; }
QListWidget#navigation {
  background: #172033; color: #e5e7eb; border: none; padding: 12px;
  font-size: 14px; outline: none;
}
QListWidget#navigation::item { padding: 12px 10px; margin: 2px 0; border-radius: 6px; }
QListWidget#navigation::item:selected { background: #2563eb; color: white; }
QFrame#broadcastStatus { background: #ffffff; border-bottom: 1px solid #d7dce3; }
QLabel#statusTitle { color: #172033; font-size: 14px; font-weight: 700; }
QLabel[status="ok"] { background: #dcfce7; color: #166534; border-radius: 9px; padding: 3px 9px; }
QLabel[status="warn"] { background: #fef3c7; color: #92400e; border-radius: 9px; padding: 3px 9px; }
QLabel[status="error"] { background: #fee2e2; color: #991b1b; border-radius: 9px; padding: 3px 9px; }
QLabel[status="neutral"] { background: #e2e8f0; color: #334155; border-radius: 9px; padding: 3px 9px; }
QLabel#liveMatch { color: #0f172a; font-weight: 600; padding: 2px 6px; }
QGroupBox { font-weight: 600; border: 1px solid #d7dce3; border-radius: 8px; margin-top: 12px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
  min-height: 30px; padding: 0 12px; color: #172033;
  background: #ffffff; border: 1px solid #cbd5e1; border-radius: 5px;
}
QPushButton:hover { background: #f1f5f9; border-color: #94a3b8; }
QPushButton:disabled { background: #e2e8f0; color: #94a3b8; border-color: #cbd5e1; }
QPushButton#primary { background: #2563eb; color: white; border: none; border-radius: 5px; }
QPushButton#danger { background: #dc2626; color: white; border: none; border-radius: 5px; }
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
  color: #0f172a; background: #ffffff; border: 1px solid #cbd5e1;
  border-radius: 4px; padding: 5px;
  selection-background-color: #2563eb; selection-color: #ffffff;
}
QComboBox QAbstractItemView {
  color: #0f172a; background: #ffffff; selection-background-color: #2563eb;
  selection-color: #ffffff; border: 1px solid #cbd5e1;
}
QSpinBox, QDoubleSpinBox { min-width: 68px; padding: 5px 24px 5px 5px; }
QSpinBox::up-button {
  subcontrol-origin: border; subcontrol-position: top right;
  width: 22px; height: 15px;
}
QSpinBox::down-button {
  subcontrol-origin: border; subcontrol-position: bottom right;
  width: 22px; height: 15px;
}
QTableWidget {
  color: #0f172a; background: #ffffff; alternate-background-color: #f7f8fa;
  gridline-color: #d7dce3; selection-background-color: #dbeafe;
  selection-color: #0f172a;
}
QHeaderView::section {
  color: #172033; background: #e9eef5; border: none;
  border-right: 1px solid #d7dce3; border-bottom: 1px solid #d7dce3;
  padding: 6px; font-weight: 600;
}
QTabWidget::pane { background: #ffffff; border: 1px solid #d7dce3; }
QTabBar::tab { color: #475569; background: #e2e8f0; padding: 7px 12px; }
QTabBar::tab:selected { color: #172033; background: #ffffff; }
QScrollArea, QScrollArea > QWidget > QWidget { background: #f4f6f9; }
QStatusBar { color: #172033; background: #ffffff; }
"""
