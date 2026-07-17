from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from .automation import AutomationEngine, ScoreboardStateStore
from .config import SettingsStore
from .detector_service import DetectorService
from .events import EventBus
from .models import AppSettings, Match
from .paths import AppPaths
from .round_rules import ALL_BO3, best_of, display_round, round_side_and_rank
from .scoreboard_server import ScoreboardServer
from .startgg import EventMatches, StartGGClient, event_slug_from_url
from .streamcontrol import StreamControlStore
from .tweet import build_match_announcement, open_x_intent


APP_STYLE = """
QMainWindow { background: #f4f6f9; }
QListWidget#navigation {
  background: #172033; color: #e5e7eb; border: none; padding: 12px;
  font-size: 14px; outline: none;
}
QListWidget#navigation::item { padding: 12px 10px; margin: 2px 0; border-radius: 6px; }
QListWidget#navigation::item:selected { background: #2563eb; color: white; }
QGroupBox { font-weight: 600; border: 1px solid #d7dce3; border-radius: 8px; margin-top: 12px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton { min-height: 30px; padding: 0 12px; }
QPushButton#primary { background: #2563eb; color: white; border: none; border-radius: 5px; }
QPushButton#danger { background: #dc2626; color: white; border: none; border-radius: 5px; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit { padding: 5px; }
QTableWidget { background: white; alternate-background-color: #f7f8fa; }
"""


class TweetDialog(QDialog):
    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("試合開始告知")
        self.resize(520, 330)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("投稿前に内容を確認・編集してください。"))
        self.preview = QTextEdit()
        self.preview.setPlainText(build_match_announcement(payload))
        layout.addWidget(self.preview)
        buttons = QHBoxLayout()
        copy_button = QPushButton("コピー")
        open_button = QPushButton("Xの投稿画面を開く")
        open_button.setObjectName("primary")
        close_button = QPushButton("閉じる")
        buttons.addWidget(copy_button)
        buttons.addWidget(open_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.preview.toPlainText()))
        open_button.clicked.connect(self.open_x)
        close_button.clicked.connect(self.accept)

    def open_x(self):
        open_x_intent(self.preview.toPlainText())


class StartGGSignals(QObject):
    loaded = Signal(int, object)
    failed = Signal(int, str)


class StartGGPage(QWidget):
    streamcontrol_updated = Signal()
    phase_rounds_loaded = Signal(object)

    def __init__(self, settings: AppSettings, store: StreamControlStore, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.matches: list[Match] = []
        self.visible_matches: list[Match] = []
        self.event_slug: str | None = None
        self._request_serial = 0
        self.signals = StartGGSignals()
        self.signals.loaded.connect(self._show_matches)
        self.signals.failed.connect(self._show_error)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.back_button = QPushButton("←")
        self.forward_button = QPushButton("→")
        self.reload_button = QPushButton("再読込")
        self.url_edit = QLineEdit(settings.startgg_home_url)
        self.go_button = QPushButton("移動")
        self.external_button = QPushButton("外部ブラウザ")
        for button, width in (
            (self.back_button, 38),
            (self.forward_button, 38),
            (self.reload_button, 68),
            (self.go_button, 52),
            (self.external_button, 92),
        ):
            button.setFixedWidth(width)
        toolbar.addWidget(self.back_button)
        toolbar.addWidget(self.forward_button)
        toolbar.addWidget(self.reload_button)
        toolbar.addWidget(self.url_edit, 1)
        toolbar.addWidget(self.go_button)
        toolbar.addWidget(self.external_button)
        layout.addLayout(toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        profile = QWebEngineProfile.defaultProfile()
        profile.setPersistentStoragePath(str(Path.home() / "AppData" / "Roaming" / "TournamentControl" / "web-profile"))
        self.web = QWebEngineView()
        self.web.setUrl(QUrl(settings.startgg_home_url))
        self.web.setMinimumWidth(0)
        self.splitter.addWidget(self.web)

        match_panel = QWidget()
        match_panel.setMinimumWidth(340)
        match_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        match_layout = QVBoxLayout(match_panel)
        match_layout.setContentsMargins(6, 0, 0, 0)
        match_layout.setSpacing(6)
        header = QHBoxLayout()
        self.event_label = QLabel("start.ggでイベントページを開いてください。")
        self.event_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.event_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.refresh_button = QPushButton("試合一覧を更新")
        header.addWidget(self.event_label, 1)
        header.addWidget(self.refresh_button)
        match_layout.addLayout(header)

        round_row = QHBoxLayout()
        round_row.addWidget(QLabel("位置"))
        self.round_selector = QComboBox()
        self.round_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.round_selector.setMinimumContentsLength(18)
        self.round_selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        round_row.addWidget(self.round_selector, 1)
        match_layout.addLayout(round_row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Player 1", "Player 2"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        match_layout.addWidget(self.table)

        action_row = QHBoxLayout()
        self.apply_button = QPushButton("StreamControlへ反映")
        self.apply_button.setObjectName("primary")
        self.apply_tweet_button = QPushButton("反映して投稿確認")
        action_row.addWidget(self.apply_button)
        action_row.addWidget(self.apply_tweet_button)
        action_row.addStretch()
        match_layout.addLayout(action_row)
        self.splitter.addWidget(match_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([720, 420])
        layout.addWidget(self.splitter, 1)

        self.back_button.clicked.connect(self.web.back)
        self.forward_button.clicked.connect(self.web.forward)
        self.reload_button.clicked.connect(self.web.reload)
        self.go_button.clicked.connect(self.navigate)
        self.url_edit.returnPressed.connect(self.navigate)
        self.external_button.clicked.connect(lambda: QDesktopServices.openUrl(self.web.url()))
        self.web.urlChanged.connect(self.on_url_changed)
        self.refresh_button.clicked.connect(self.refresh_matches)
        self.round_selector.currentIndexChanged.connect(self._filter_matches)
        self.apply_button.clicked.connect(lambda: self.apply_selected(False))
        self.apply_tweet_button.clicked.connect(lambda: self.apply_selected(True))

    def set_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def navigate(self) -> None:
        raw = self.url_edit.text().strip()
        if raw and not raw.startswith(("http://", "https://")):
            raw = "https://" + raw
        self.web.setUrl(QUrl(raw))

    def on_url_changed(self, url: QUrl) -> None:
        raw = url.toString()
        self.url_edit.setText(raw)
        slug = event_slug_from_url(raw)
        if slug and slug != self.event_slug:
            self.event_slug = slug
            self.event_label.setText(f"イベント: {slug}（API読込待ち）")
            self.refresh_matches()

    def refresh_matches(self) -> None:
        if not self.event_slug:
            self.event_label.setText("start.ggでイベントページを開いてください。")
            return
        self._request_serial += 1
        serial = self._request_serial
        slug = self.event_slug
        token = self.settings.startgg_token
        self.refresh_button.setEnabled(False)
        self.event_label.setText(f"{slug} を読み込み中…")

        def fetch():
            try:
                result = StartGGClient(token).event_matches(slug)
            except Exception as exc:
                self.signals.failed.emit(serial, str(exc))
                return
            self.signals.loaded.emit(serial, result)

        threading.Thread(target=fetch, daemon=True, name="startgg-fetch").start()

    def _show_error(self, serial: int, message: str) -> None:
        if serial != self._request_serial:
            return
        self.refresh_button.setEnabled(True)
        self.event_label.setText("試合一覧を取得できませんでした。")
        QMessageBox.warning(self, "start.gg", message)

    def _show_matches(self, serial: int, event: EventMatches) -> None:
        if serial != self._request_serial:
            return
        splitter_sizes = self.splitter.sizes()
        self.refresh_button.setEnabled(True)
        phase_catalog: dict[str, dict] = {}
        for match in event.matches:
            phase_id = match.phase_id or "event"
            entry = phase_catalog.setdefault(
                phase_id,
                {"name": match.phase_name or event.event_name, "rounds": []},
            )
            if match.round_text and match.round_text not in entry["rounds"]:
                entry["rounds"].append(match.round_text)
        self.phase_rounds_loaded.emit(phase_catalog)
        self.matches = [match for match in event.matches if match.selectable]
        self.event_label.setText(
            f"{event.event_name} — 選択可能 {len(self.matches)}試合 / 全{len(event.matches)}試合"
        )
        current_round = self.round_selector.currentData()
        self.round_selector.blockSignals(True)
        self.round_selector.clear()
        seen_rounds: set[tuple[str, str]] = set()
        for match in self.matches:
            round_key = (match.phase_id or "event", match.round_text)
            if round_key in seen_rounds:
                continue
            seen_rounds.add(round_key)
            label = display_round(match.round_text, self.settings.round_mappings)
            phase_prefix = f"{match.phase_name} / " if match.phase_name else ""
            self.round_selector.addItem(
                f"{phase_prefix}{label}  ({match.round_text})",
                round_key,
            )
        if current_round:
            index = self.round_selector.findData(current_round)
            if index >= 0:
                self.round_selector.setCurrentIndex(index)
        self.round_selector.blockSignals(False)
        self._filter_matches()
        if sum(splitter_sizes) > 0:
            QTimer.singleShot(0, lambda sizes=splitter_sizes: self.splitter.setSizes(sizes))

    def _filter_matches(self) -> None:
        selected_round = self.round_selector.currentData()
        self.visible_matches = [
            match
            for match in self.matches
            if (match.phase_id or "event", match.round_text) == selected_round
        ]
        self.table.setRowCount(len(self.visible_matches))
        status_names = {1: "未開始", 2: "進行中", 3: "完了"}
        for row, match in enumerate(self.visible_matches):
            names = [player.gamer_tag for player in match.players]
            phase_rule = self.settings.phase_bo5_rules.get(match.phase_id or "event", {})
            calculated_bo = best_of(
                match.round_text,
                phase_rule.get("winners_from", self.settings.winners_bo5_from),
                phase_rule.get("losers_from", self.settings.losers_bo5_from),
            )
            values = [
                names[0] if len(names) > 0 else "TBD",
                names[1] if len(names) > 1 else "TBD",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    f"{match.round_text} / BO{calculated_bo} / "
                    f"{status_names.get(match.state, str(match.state))} / Set {match.set_id}"
                )
                self.table.setItem(row, column, item)

    def apply_selected(self, open_tweet: bool) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.visible_matches):
            QMessageBox.information(self, "試合選択", "反映する試合を選択してください。")
            return
        match = self.visible_matches[row]
        phase_rule = self.settings.phase_bo5_rules.get(match.phase_id or "event", {})
        winners_from = phase_rule.get("winners_from", self.settings.winners_bo5_from)
        losers_from = phase_rule.get("losers_from", self.settings.losers_bo5_from)
        try:
            payload = self.store.apply_match(
                match,
                winners_bo5_from=winners_from,
                losers_bo5_from=losers_from,
                round_mappings=self.settings.round_mappings,
                stream_url=self.settings.stream_url,
            )
        except Exception as exc:
            QMessageBox.warning(self, "反映失敗", str(exc))
            return
        self.streamcontrol_updated.emit()
        if open_tweet:
            TweetDialog(payload, self).exec()


class StreamControlPage(QWidget):
    def __init__(self, store: StreamControlStore, scoreboard_url: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.scoreboard_url = scoreboard_url
        self._loading = False
        self._dirty = False
        self._last_mtime = 0.0
        self.fields: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Best of"))
        self.best_of = QComboBox()
        for value in (1, 3, 5, 7, 9, 11, 13, 15, 17, 19):
            self.best_of.addItem(str(value), f"bo{value}")
        top.addWidget(self.best_of)
        top.addStretch()
        self.open_scoreboard = QPushButton("Scoreboardをブラウザで確認")
        self.copy_scoreboard = QPushButton("OBS URLをコピー")
        top.addWidget(self.open_scoreboard)
        top.addWidget(self.copy_scoreboard)
        layout.addLayout(top)

        tabs = QTabWidget()
        match_tab = QWidget()
        match_layout = QVBoxLayout(match_tab)
        player_grid = QGridLayout()
        headers = ["", "Team", "Name", "Twitter", "Country", "Score"]
        for col, header in enumerate(headers):
            player_grid.addWidget(QLabel(header), 0, col)
        for side in (1, 2):
            player_grid.addWidget(QLabel(f"P{side}"), side, 0)
            for col, stem in enumerate(("pTeam", "pName", "pTwitter", "pCountry"), start=1):
                edit = QLineEdit()
                key = f"{stem}{side}"
                self.fields[key] = edit
                player_grid.addWidget(edit, side, col)
            score = QSpinBox()
            score.setRange(0, 9)
            self.fields[f"pScore{side}"] = score
            player_grid.addWidget(score, side, 5)
        match_layout.addLayout(player_grid)

        buttons = QHBoxLayout()
        for label, handler in (
            ("Clear P1", lambda: self.clear_player(1)),
            ("Clear P2", lambda: self.clear_player(2)),
            ("Reset Scores", self.reset_scores),
            ("Swap", self.swap_players),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        match_layout.addLayout(buttons)

        form = QFormLayout()
        self.stage = QLineEdit()
        self.stream_url = QLineEdit()
        form.addRow("Stage", self.stage)
        form.addRow("Stream URL", self.stream_url)
        match_layout.addLayout(form)

        action_row = QHBoxLayout()
        self.save_button = QPushButton("StreamControlへ保存")
        self.save_button.setObjectName("primary")
        self.sponsors_button = QPushButton("Sponsors表示")
        self.tweet_button = QPushButton("保存して投稿確認")
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.sponsors_button)
        action_row.addWidget(self.tweet_button)
        action_row.addStretch()
        match_layout.addLayout(action_row)
        tabs.addTab(match_tab, "Match")

        l3rd_tab = QWidget()
        l3rd_form = QFormLayout(l3rd_tab)
        for key, label in (
            ("l3rd1a", "Lower Third 1 / Line 1"),
            ("l3rd1b", "Lower Third 1 / Line 2"),
            ("l3rd2a", "Lower Third 2 / Line 1"),
            ("l3rd2b", "Lower Third 2 / Line 2"),
        ):
            edit = QLineEdit()
            self.fields[key] = edit
            l3rd_form.addRow(label, edit)
        tabs.addTab(l3rd_tab, "Lower Thirds")
        layout.addWidget(tabs)

        self.status = QLabel()
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)

        for widget in list(self.fields.values()) + [self.stage, self.stream_url]:
            if isinstance(widget, QLineEdit):
                widget.textEdited.connect(self.mark_dirty)
            else:
                widget.valueChanged.connect(self.mark_dirty)
        self.best_of.currentIndexChanged.connect(self.mark_dirty)
        self.save_button.clicked.connect(self.save)
        self.sponsors_button.clicked.connect(self.show_sponsors)
        self.tweet_button.clicked.connect(self.open_tweet_dialog)
        self.open_scoreboard.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.scoreboard_url)))
        self.copy_scoreboard.clicked.connect(lambda: QApplication.clipboard().setText(self.scoreboard_url))

        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.poll_external_update)
        self.timer.start()
        self.load()

    def set_scoreboard_url(self, url: str) -> None:
        self.scoreboard_url = url

    def mark_dirty(self, *args) -> None:
        if not self._loading:
            self._dirty = True
            self.status.setText("未保存の変更があります。")

    def load(self) -> None:
        payload = self.store.read()
        self._loading = True
        try:
            index = self.best_of.findData(payload.get("bestOf", "bo3"))
            self.best_of.setCurrentIndex(max(0, index))
            for key, widget in self.fields.items():
                value = payload.get(key, "0" if key.startswith("pScore") else "")
                if isinstance(widget, QSpinBox):
                    try:
                        widget.setValue(int(str(value)))
                    except ValueError:
                        widget.setValue(0)
                else:
                    widget.setText(str(value))
            self.stage.setText(str(payload.get("stage", "")))
            self.stream_url.setText(str(payload.get("streamUrl", "")))
        finally:
            self._loading = False
        self._dirty = False
        self._last_mtime = self.store.path.stat().st_mtime if self.store.path.exists() else 0.0
        self.status.setText(f"Scoreboard URL: {self.scoreboard_url}")

    def poll_external_update(self) -> None:
        if not self.store.path.exists():
            return
        mtime = self.store.path.stat().st_mtime
        if mtime <= self._last_mtime:
            return
        payload = self.store.read()
        self._last_mtime = mtime
        self._loading = True
        try:
            for side in (1, 2):
                widget = self.fields[f"pScore{side}"]
                widget.setValue(int(str(payload.get(f"pScore{side}", "0")) or "0"))
            if not self._dirty:
                self.load()
        finally:
            self._loading = False

    def collect(self) -> dict:
        payload = self.store.read()
        payload["bestOf"] = self.best_of.currentData()
        for key, widget in self.fields.items():
            payload[key] = str(widget.value()) if isinstance(widget, QSpinBox) else widget.text()
        payload["stage"] = self.stage.text()
        payload["streamUrl"] = self.stream_url.text()
        return payload

    def save(self) -> None:
        self.store.write(self.collect())
        self.load()
        self.status.setText("StreamControlを更新しました。")

    def clear_player(self, side: int) -> None:
        for stem in ("pTeam", "pName", "pTwitter", "pCountry"):
            self.fields[f"{stem}{side}"].clear()
        self.fields[f"pScore{side}"].setValue(0)
        self.mark_dirty()

    def reset_scores(self) -> None:
        self.fields["pScore1"].setValue(0)
        self.fields["pScore2"].setValue(0)
        self.mark_dirty()

    def swap_players(self) -> None:
        for stem in ("pTeam", "pName", "pTwitter", "pCountry", "pScore"):
            left, right = self.fields[f"{stem}1"], self.fields[f"{stem}2"]
            if isinstance(left, QSpinBox):
                left_value, right_value = left.value(), right.value()
                left.setValue(right_value)
                right.setValue(left_value)
            else:
                left_value, right_value = left.text(), right.text()
                left.setText(right_value)
                right.setText(left_value)
        self.mark_dirty()

    def show_sponsors(self) -> None:
        payload = self.collect()
        self.store.write_with_sponsors(payload)
        self.load()
        self.status.setText("Sponsors表示トリガーを更新しました。")

    def open_tweet_dialog(self) -> None:
        payload = self.collect()
        self.store.write(payload)
        self.load()
        TweetDialog(payload, self).exec()


class DetectorSignals(QObject):
    log = Signal(str)
    score_updated = Signal()


class DetectorPage(QWidget):
    score_updated = Signal()

    def __init__(
        self,
        settings: AppSettings,
        paths: AppPaths,
        event_bus: EventBus | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.paths = paths
        self.event_bus = event_bus
        self.service: DetectorService | None = None
        self.signals = DetectorSignals()
        self.signals.log.connect(self.append_log)
        self.signals.score_updated.connect(self.score_updated.emit)

        layout = QVBoxLayout(self)
        config_group = QGroupBox("OBS接続")
        form = QFormLayout(config_group)
        self.host = QLineEdit(settings.obs_host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(settings.obs_port)
        self.password = QLineEdit(settings.obs_password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.source = QLineEdit(settings.obs_source_name)
        self.write_scores = QCheckBox("検出した勝者のスコアを自動加算")
        self.write_scores.setChecked(settings.detector_write_scores)
        form.addRow("Host", self.host)
        form.addRow("Port", self.port)
        form.addRow("Password", self.password)
        form.addRow("Source", self.source)
        form.addRow("", self.write_scores)
        layout.addWidget(config_group)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Detector開始")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("danger")
        self.stop_button.setEnabled(False)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        self.start_button.clicked.connect(self.start_detector)
        self.stop_button.clicked.connect(self.stop_detector)

    def set_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        if line.startswith("Detectorを停止") or line.startswith("Detector停止"):
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def start_detector(self) -> None:
        self.service = DetectorService(
            detector_dir=self.paths.detector_dir,
            monitor_config_path=self.paths.detector_runtime_config,
            streamcontrol_path=self.paths.streamcontrol_file,
            obs_host=self.host.text().strip(),
            obs_port=self.port.value(),
            obs_password=self.password.text(),
            source_name=self.source.text().strip(),
            write_scores=self.write_scores.isChecked(),
            on_log=self.signals.log.emit,
            on_score=self.signals.score_updated.emit,
            on_event=self.event_bus.publish if self.event_bus else None,
        )
        self.service.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.append_log("Detectorを開始しています…")

    def stop_detector(self) -> None:
        if self.service:
            self.service.stop()
        self.stop_button.setEnabled(False)


class SettingsPage(QWidget):
    saved = Signal(object)

    ROUND_MAPPING_OPTIONS = [
        ("Grand Final", "GF (Grand Final)", "GF"),
        ("Grand Final Reset", "GFR (Grand Final Reset)", "GFR"),
        ("Winners Final", "WF (Winners Final)", "WF"),
        ("Winners Semifinal", "WSF (Winners Semi-Final)", "WSF"),
        ("Winners Quarterfinal", "WQF (Winners Quarter-Final)", "WQF"),
        ("Losers Final", "LF (Losers Final)", "LF"),
        ("Losers Semifinal", "LSF (Losers Semi-Final)", "LSF"),
        ("Losers Quarterfinal", "LQF (Losers Quarter-Final)", "LQF"),
        ("Winners Round {n}", 'WR"n" (Winners Round "n")', "WR{n}"),
        ("Losers Round {n}", 'LR"n" (Losers Round "n")', "LR{n}"),
    ]

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.phase_catalog: dict[str, dict] = {}
        self.phase_rules: dict[str, dict[str, str]] = {}
        self.phase_controls: dict[str, tuple[QComboBox, QComboBox]] = {}
        self.fallback_winners = settings.winners_bo5_from
        self.fallback_losers = settings.losers_bo5_from
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        settings_content = QWidget()
        layout = QVBoxLayout(settings_content)
        layout.setContentsMargins(4, 4, 8, 8)
        layout.setSpacing(8)

        startgg_group = QGroupBox("start.gg")
        startgg_form = QFormLayout(startgg_group)
        self.home_url = QLineEdit()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.stream_url = QLineEdit()
        for field in (self.home_url, self.token, self.stream_url):
            field.setMinimumHeight(32)
        startgg_form.addRow("開始URL", self.home_url)
        startgg_form.addRow("API Token", self.token)
        startgg_form.addRow("配信URL", self.stream_url)
        layout.addWidget(startgg_group)

        phase_group = QGroupBox("PhaseごとのBO5開始位置")
        phase_layout = QVBoxLayout(phase_group)
        phase_layout.addWidget(QLabel("start.gg管理で試合一覧を更新すると、実在するPhaseとラウンドだけが表示されます。"))
        phase_layout.addWidget(QLabel("Phase全体をBO3にする場合は、WinnersとLosersの両方で「すべてBO3」を選択します。"))
        self.phase_tabs = QTabWidget()
        self.phase_tabs.setMinimumHeight(140)
        phase_layout.addWidget(self.phase_tabs)
        layout.addWidget(phase_group)

        mapping_group = QGroupBox("試合位置の表示名")
        mapping_layout = QFormLayout(mapping_group)
        self.mapping_fields: dict[str, QLineEdit] = {}
        for source, label, _default in self.ROUND_MAPPING_OPTIONS:
            field = QLineEdit()
            field.setMinimumHeight(32)
            self.mapping_fields[source] = field
            mapping_layout.addRow(label, field)
        layout.addWidget(mapping_group)

        local_group = QGroupBox("ローカル")
        local_form = QFormLayout(local_group)
        self.scoreboard_port = QSpinBox()
        self.scoreboard_port.setRange(1024, 65535)
        self.scoreboard_port.setMinimumHeight(32)
        local_form.addRow("Scoreboard HTTP Port", self.scoreboard_port)
        obs_url_row = QWidget()
        obs_url_layout = QHBoxLayout(obs_url_row)
        obs_url_layout.setContentsMargins(0, 0, 0, 0)
        self.obs_url = QLineEdit()
        self.obs_url.setReadOnly(True)
        self.obs_url.setMinimumHeight(32)
        self.copy_obs_url = QPushButton("コピー")
        obs_url_layout.addWidget(self.obs_url, 1)
        obs_url_layout.addWidget(self.copy_obs_url)
        local_form.addRow("OBS Browser Source URL", obs_url_row)
        layout.addWidget(local_group)
        layout.addStretch()

        self.scroll_area.setWidget(settings_content)
        outer_layout.addWidget(self.scroll_area, 1)

        self.save_button = QPushButton("設定を保存")
        self.save_button.setObjectName("primary")
        outer_layout.addWidget(self.save_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.save_button.clicked.connect(self.save)
        self.scoreboard_port.valueChanged.connect(self.update_obs_url)
        self.copy_obs_url.clicked.connect(lambda: QApplication.clipboard().setText(self.obs_url.text()))
        self.load(settings)

    def load(self, settings: AppSettings) -> None:
        self.home_url.setText(settings.startgg_home_url)
        self.token.setText(settings.startgg_token)
        self.fallback_winners = settings.winners_bo5_from
        self.fallback_losers = settings.losers_bo5_from
        self.stream_url.setText(settings.stream_url)
        self.scoreboard_port.setValue(settings.scoreboard_port)
        for source, _label, default in self.ROUND_MAPPING_OPTIONS:
            target = settings.round_mappings.get(source)
            if not target and "{n}" not in source:
                target = display_round(source, settings.round_mappings)
            self.mapping_fields[source].setText(str(target or default))
        self.update_phase_catalog(settings.phase_round_options, settings.phase_bo5_rules)
        self.update_obs_url()

    def _capture_phase_rules(self) -> None:
        for phase_id, (winners, losers) in self.phase_controls.items():
            self.phase_rules[phase_id] = {
                "winners_from": str(winners.currentData()),
                "losers_from": str(losers.currentData()),
            }

    def update_phase_catalog(
        self,
        catalog: dict[str, dict],
        saved_rules: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._capture_phase_rules()
        self.phase_catalog = dict(catalog or {})
        if saved_rules:
            self.phase_rules.update(saved_rules)
        self.phase_controls.clear()
        while self.phase_tabs.count():
            page = self.phase_tabs.widget(0)
            self.phase_tabs.removeTab(0)
            page.deleteLater()

        if not self.phase_catalog:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.addWidget(QLabel("試合一覧をまだ取得していません。"))
            page_layout.addStretch()
            self.phase_tabs.addTab(page, "未取得")
            return

        for phase_id, info in self.phase_catalog.items():
            rounds = [str(value) for value in info.get("rounds", [])]
            winners_rounds = sorted(
                {value for value in rounds if round_side_and_rank(value)[0] == "winners"},
                key=lambda value: round_side_and_rank(value)[1],
                reverse=True,
            )
            losers_rounds = sorted(
                {value for value in rounds if round_side_and_rank(value)[0] == "losers"},
                key=lambda value: round_side_and_rank(value)[1],
                reverse=True,
            )
            page = QWidget()
            form = QFormLayout(page)
            winners = QComboBox()
            losers = QComboBox()
            for combo, candidates in ((winners, winners_rounds), (losers, losers_rounds)):
                combo.addItem("すべてBO3", ALL_BO3)
                for round_text in candidates:
                    short_name = display_round(round_text, self.mapping_fields_value())
                    combo.addItem(f"{short_name} ({round_text}) 以降BO5", round_text)
                combo.setMinimumHeight(32)

            existing = self.phase_rules.get(phase_id, {})
            winners_default = next(
                (value for value in winners_rounds if round_side_and_rank(value)[1] == 100),
                ALL_BO3,
            )
            losers_default = next(
                (value for value in losers_rounds if round_side_and_rank(value)[1] == 100),
                ALL_BO3,
            )
            winners_value = existing.get("winners_from", winners_default)
            losers_value = existing.get("losers_from", losers_default)
            winners.setCurrentIndex(max(0, winners.findData(winners_value)))
            losers.setCurrentIndex(max(0, losers.findData(losers_value)))
            form.addRow("Winners", winners)
            form.addRow("Losers", losers)
            self.phase_controls[phase_id] = (winners, losers)
            self.phase_tabs.addTab(page, str(info.get("name") or phase_id))

    def mapping_fields_value(self) -> dict[str, str]:
        return {
            source: self.mapping_fields[source].text().strip() or default
            for source, _label, default in self.ROUND_MAPPING_OPTIONS
        }

    def update_obs_url(self, *args) -> None:
        self.obs_url.setText(f"http://127.0.0.1:{self.scoreboard_port.value()}/scoreboard.html")

    def save(self) -> None:
        mappings = self.mapping_fields_value()
        self._capture_phase_rules()
        settings = AppSettings(
            startgg_home_url=self.home_url.text().strip() or "https://www.start.gg/",
            startgg_token=self.token.text().strip(),
            winners_bo5_from=self.fallback_winners,
            losers_bo5_from=self.fallback_losers,
            round_mappings=mappings,
            phase_bo5_rules=dict(self.phase_rules),
            phase_round_options=dict(self.phase_catalog),
            scoreboard_port=self.scoreboard_port.value(),
            stream_url=self.stream_url.text().strip(),
        )
        self.saved.emit(settings)


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.settings_store = SettingsStore(paths.settings_file)
        self.settings = self.settings_store.load()
        self.store = StreamControlStore(paths.streamcontrol_file)
        self.event_bus = EventBus()
        self.scoreboard_state = ScoreboardStateStore(paths.scoreboard_state_file)
        self.automation = AutomationEngine(
            self.event_bus,
            paths.automation_rules_file,
            self.scoreboard_state,
        )
        self.server = ScoreboardServer(
            paths.scoreboard_assets,
            self.store,
            self.settings.scoreboard_port,
            self.scoreboard_state,
        )
        try:
            self.server.start()
        except OSError as exc:
            QMessageBox.critical(self, "Scoreboard Server", f"Scoreboardサーバーを開始できません: {exc}")

        self.setWindowTitle("Tournament Control")
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(1440, int(available.width() * 0.92)), min(920, int(available.height() * 0.92)))
        else:
            self.resize(1280, 800)
        self.setStyleSheet(APP_STYLE)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(170)
        self.navigation.addItems(["start.gg 管理", "StreamControl", "Score Detector", "設定"])
        self.stack = QStackedWidget()

        self.startgg_page = StartGGPage(self.settings, self.store)
        self.streamcontrol_page = StreamControlPage(self.store, self.server.scoreboard_url)
        self.detector_page = DetectorPage(self.settings, paths, self.event_bus)
        self.settings_page = SettingsPage(self.settings)
        for page in (self.startgg_page, self.streamcontrol_page, self.detector_page, self.settings_page):
            self.stack.addWidget(page)
        layout.addWidget(self.navigation)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.startgg_page.streamcontrol_updated.connect(self.streamcontrol_page.load)
        self.startgg_page.phase_rounds_loaded.connect(self.update_phase_catalog)
        self.detector_page.score_updated.connect(self.streamcontrol_page.load)
        self.settings_page.saved.connect(self.save_settings)
        self.statusBar().showMessage(f"OBS Browser Source: {self.server.scoreboard_url}")

    def update_phase_catalog(self, catalog: dict[str, dict]) -> None:
        self.settings.phase_round_options = dict(catalog)
        self.settings_page.update_phase_catalog(catalog)

    def save_settings(self, new_settings: AppSettings) -> None:
        # Detector connection fields live on its page and are included when settings are saved.
        new_settings.obs_host = self.detector_page.host.text().strip()
        new_settings.obs_port = self.detector_page.port.value()
        new_settings.obs_password = self.detector_page.password.text()
        new_settings.obs_source_name = self.detector_page.source.text().strip()
        new_settings.detector_write_scores = self.detector_page.write_scores.isChecked()
        old_port = self.settings.scoreboard_port
        self.settings = new_settings
        self.settings_store.save(new_settings)
        self.startgg_page.set_settings(new_settings)
        self.detector_page.set_settings(new_settings)

        if old_port != new_settings.scoreboard_port:
            self.server.stop()
            self.server = ScoreboardServer(
                self.paths.scoreboard_assets,
                self.store,
                new_settings.scoreboard_port,
                self.scoreboard_state,
            )
            try:
                self.server.start()
            except OSError as exc:
                QMessageBox.warning(self, "設定", f"新しいポートでScoreboardを開始できません: {exc}")
            self.streamcontrol_page.set_scoreboard_url(self.server.scoreboard_url)
        self.statusBar().showMessage(f"設定を保存しました。OBS Browser Source: {self.server.scoreboard_url}", 8000)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.detector_page.stop_detector()
        self.server.stop()
        self.automation.close()
        event.accept()
