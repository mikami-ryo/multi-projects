from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .obs_layout import layout_preview_entries


class ScrollSafeSpinBox(QSpinBox):
    """Keep trackpad scrolling for the containing page, not value changes."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class ScrollSafeDoubleSpinBox(QDoubleSpinBox):
    """Keep trackpad scrolling for the containing page, not value changes."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class StatusBadge(QLabel):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.set_status("neutral", "確認待ち")

    def set_status(self, status: str, text: str) -> None:
        self.setText(f"{self.title}: {text}")
        self.setProperty("status", status)
        self.style().unpolish(self)
        self.style().polish(self)


class BroadcastStatusStrip(QFrame):
    """Always-visible operational summary for the live broadcast."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("broadcastStatus")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 7, 12, 7)
        outer.setSpacing(4)
        status_row = QHBoxLayout()
        title = QLabel("配信ステータス")
        title.setObjectName("statusTitle")
        status_row.addWidget(title)
        self.server = StatusBadge("Scoreboard")
        self.detector = StatusBadge("Detector")
        self.presentation = StatusBadge("表示")
        for badge in (self.server, self.detector, self.presentation):
            status_row.addWidget(badge)
        status_row.addStretch()
        outer.addLayout(status_row)
        self.match = QLabel("試合: 未設定")
        self.match.setObjectName("liveMatch")
        self.match.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.match)

    def set_server(self, running: bool, url: str) -> None:
        self.server.set_status("ok" if running else "error", "稼働中" if running else "停止")
        self.server.setToolTip(url)

    def set_detector(self, state: str) -> None:
        labels = {
            "running": ("ok", "監視中"),
            "starting": ("warn", "接続中"),
            "stopping": ("warn", "停止処理中"),
            "error": ("error", "要確認"),
            "stopped": ("neutral", "停止"),
        }
        self.detector.set_status(*labels.get(state, ("neutral", state)))

    def set_presentation(self, mode: str, scene: str) -> None:
        mode_label = "自動" if mode == "automatic" else "固定"
        scene_label = "試合間" if scene == "between_game" else "試合中"
        self.presentation.set_status("ok" if mode == "automatic" else "warn", f"{mode_label} / {scene_label}")

    def set_match(self, payload: dict) -> None:
        left = str(payload.get("pName1") or "未設定")
        right = str(payload.get("pName2") or "未設定")
        left_score = str(payload.get("pScore1") or "0")
        right_score = str(payload.get("pScore2") or "0")
        stage = str(payload.get("stage") or "位置未設定")
        best_of = str(payload.get("bestOf") or "bo3").upper()
        self.match.setText(f"試合: {left}  {left_score} - {right_score}  {right}　｜　{stage} / {best_of}")


class LayoutPreview(QWidget):
    """Small schematic of the OBS transforms saved in a layout profile."""

    COLORS = {
        "game": QColor("#2563eb"),
        "player1": QColor("#db2777"),
        "player2": QColor("#7c3aed"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile: dict[str, dict] = {}
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("保存されている位置・大きさ・重なり順の模式図です。")

    def set_profile(self, profile: dict[str, dict]) -> None:
        self.profile = dict(profile or {})
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#e2e8f0"))
        available = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        canvas_width = available.width()
        canvas_height = canvas_width * 9.0 / 16.0
        if canvas_height > available.height():
            canvas_height = available.height()
            canvas_width = canvas_height * 16.0 / 9.0
        canvas = QRectF(
            available.center().x() - canvas_width / 2,
            available.center().y() - canvas_height / 2,
            canvas_width,
            canvas_height,
        )
        painter.fillRect(canvas, QColor("#0f172a"))
        painter.setPen(QPen(QColor("#64748b"), 1))
        painter.drawRect(canvas)
        entries = layout_preview_entries(self.profile)
        if not entries:
            painter.setPen(QColor("#cbd5e1"))
            painter.drawText(canvas, Qt.AlignmentFlag.AlignCenter, "レイアウト未保存")
            return
        painter.save()
        painter.setClipRect(canvas)
        for entry in entries:
            item_rect = QRectF(
                canvas.left() + entry["x"] * canvas.width(),
                canvas.top() + entry["y"] * canvas.height(),
                max(3.0, entry["width"] * canvas.width()),
                max(3.0, entry["height"] * canvas.height()),
            )
            color = QColor(self.COLORS.get(entry["key"], QColor("#0891b2")))
            color.setAlpha(145 if entry["enabled"] else 45)
            painter.fillRect(item_rect, color)
            painter.setPen(QPen(QColor("#f8fafc"), 2 if entry["enabled"] else 1))
            painter.drawRect(item_rect)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                item_rect.adjusted(5, 3, -5, -3),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                f'{entry["source"]}  (layer {entry["layer"]})',
            )
        painter.restore()
