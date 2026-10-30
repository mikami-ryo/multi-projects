from __future__ import annotations

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
    QDoubleSpinBox,
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
from .models import AppSettings, Match, Player
from .obs_layout import (
    ObsLayoutController,
)
from .obs_transition import BrowserTransitionController, TransitionStateStore
from .operations import LatestTaskRunner, ObsLayoutOperations, StartGGOperations
from .paths import AppPaths
from .round_rules import ALL_BO3, best_of, display_round, round_side_and_rank
from .scoreboard_server import ScoreboardServer
from .startgg import (
    EventMatches,
    event_slug_from_url,
    phase_group_id_from_url,
)
from .streamcontrol import ScoreLimitError, StreamControlStore, score_limits
from .tweet import build_match_announcement, open_x_intent
from .ui_components import (
    BroadcastStatusStrip,
    LayoutPreview,
    ScrollSafeDoubleSpinBox,
    ScrollSafeSpinBox,
)
from .ui_theme import APP_STYLE


ALL_ROUNDS_KEY = ("__all_rounds__", "__all_rounds__")


def round_keys_for_matches(matches: list[Match], bracket_type: str) -> list[tuple[str, str]]:
    if bracket_type.upper() == "ROUND_ROBIN":
        return [ALL_ROUNDS_KEY] if matches else []
    keys: list[tuple[str, str]] = []
    for match in matches:
        key = (match.phase_id or "event", match.round_text)
        if key not in keys:
            keys.append(key)
    return keys


def matches_for_round_key(
    matches: list[Match],
    bracket_type: str,
    selected_round: tuple[str, str] | None,
) -> list[Match]:
    if bracket_type.upper() == "ROUND_ROBIN" and selected_round == ALL_ROUNDS_KEY:
        return list(matches)
    return [
        match
        for match in matches
        if (match.phase_id or "event", match.round_text) == selected_round
    ]


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
    details_loaded = Signal(int, object, object, bool)
    details_failed = Signal(int, object, str, bool)


class StartGGPage(QWidget):
    streamcontrol_updated = Signal()
    phase_rounds_loaded = Signal(object)

    def __init__(self, settings: AppSettings, store: StreamControlStore, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.matches: list[Match] = []
        self.visible_matches: list[Match] = []
        self.bracket_type = ""
        self.event_slug: str | None = None
        self.phase_group_id: str | None = None
        self._request_serial = 0
        self._details_serial = 0
        self._detailed_set_ids: set[str] = set()
        self.operations = StartGGOperations()
        self._match_runner = LatestTaskRunner("startgg-list-worker")
        self._details_runner = LatestTaskRunner("startgg-details-worker")
        self.signals = StartGGSignals()
        self.signals.loaded.connect(self._show_matches)
        self.signals.failed.connect(self._show_error)
        self.signals.details_loaded.connect(self._finish_player_details)
        self.signals.details_failed.connect(self._player_details_failed)

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

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Round", "状態", "Player 1", "Player 2", "形式"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
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
        phase_group_id = phase_group_id_from_url(raw)
        if not phase_group_id and slug == self.event_slug:
            # Score reporting can temporarily navigate away from the bracket
            # URL. Keep filtering to the bracket the operator selected until a
            # different bracket (or event) is opened explicitly.
            phase_group_id = self.phase_group_id
        if slug and (
            slug != self.event_slug or phase_group_id != self.phase_group_id
        ):
            self.event_slug = slug
            self.phase_group_id = phase_group_id
            self.event_label.setText(f"イベント: {slug}（API読込待ち）")
            self.refresh_matches()

    def refresh_matches(self) -> None:
        if not self.event_slug:
            self.event_label.setText("start.ggでイベントページを開いてください。")
            return
        self._request_serial += 1
        serial = self._request_serial
        self._details_serial += 1
        self.apply_button.setEnabled(True)
        self.apply_tweet_button.setEnabled(True)
        slug = self.event_slug
        phase_group_id = self.phase_group_id
        token = self.settings.startgg_token
        self.refresh_button.setEnabled(False)
        self.event_label.setText(f"{slug} を読み込み中…")

        self._match_runner.submit(
            lambda: self.operations.load_matches(token, slug, phase_group_id),
            lambda result: self.signals.loaded.emit(serial, result),
            lambda message: self.signals.failed.emit(serial, message),
        )

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
        self._detailed_set_ids.intersection_update(match.set_id for match in self.matches)
        self.bracket_type = event.bracket_type
        self.event_label.setText(
            f"{event.event_name} — 選択可能 {len(self.matches)}試合 / 全{len(event.matches)}試合"
        )
        current_round = self.round_selector.currentData()
        self.round_selector.blockSignals(True)
        self.round_selector.clear()
        for round_key in round_keys_for_matches(self.matches, self.bracket_type):
            if round_key == ALL_ROUNDS_KEY:
                self.round_selector.addItem(f"{event.event_name} / 全Round", round_key)
                continue
            match = next(
                item
                for item in self.matches
                if (item.phase_id or "event", item.round_text) == round_key
            )
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
        self.visible_matches = matches_for_round_key(
            self.matches,
            self.bracket_type,
            selected_round,
        )
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
                display_round(match.round_text, self.settings.round_mappings),
                status_names.get(match.state, str(match.state)),
                names[0] if len(names) > 0 else "TBD",
                names[1] if len(names) > 1 else "TBD",
                f"BO{calculated_bo}",
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
        if match.set_id not in self._detailed_set_ids:
            self._details_serial += 1
            serial = self._details_serial
            self.apply_button.setEnabled(False)
            self.apply_tweet_button.setEnabled(False)
            self.event_label.setText("選択した試合の選手詳細を読み込み中…")
            token = self.settings.startgg_token
            self._details_runner.submit(
                lambda: self.operations.load_players(token, match.set_id),
                lambda players: self.signals.details_loaded.emit(
                    serial, match, players, open_tweet
                ),
                lambda message: self.signals.details_failed.emit(
                    serial, match, message, open_tweet
                ),
            )
            return
        self._apply_match(match, open_tweet)

    def _finish_player_details(
        self, serial: int, match: Match, players: list[Player], open_tweet: bool
    ) -> None:
        if serial != self._details_serial or match not in self.matches:
            return
        self.apply_button.setEnabled(True)
        self.apply_tweet_button.setEnabled(True)
        if len(players) >= 2:
            match.players = players
        self._detailed_set_ids.add(match.set_id)
        self._apply_match(match, open_tweet)

    def _player_details_failed(
        self, serial: int, match: Match, message: str, open_tweet: bool
    ) -> None:
        if serial != self._details_serial or match not in self.matches:
            return
        self.apply_button.setEnabled(True)
        self.apply_tweet_button.setEnabled(True)
        self._detailed_set_ids.add(match.set_id)
        self.event_label.setText(
            f"選手のSNS情報を取得できなかったため、現在の名前で反映します: {message}"
        )
        self._apply_match(match, open_tweet)

    def close_operations(self) -> None:
        self._match_runner.close()
        self._details_runner.close()

    def _apply_match(self, match: Match, open_tweet: bool) -> None:
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
    def __init__(
        self,
        store: StreamControlStore,
        scoreboard_url: str,
        state_store: ScoreboardStateStore | None = None,
        on_scene_change=None,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.state_store = state_store
        self.on_scene_change = on_scene_change or (lambda _scene: None)
        self.scoreboard_url = scoreboard_url
        self._loading = False
        self._dirty = False
        self._last_mtime = 0.0
        self.fields: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("試合形式"))
        self.best_of = QComboBox()
        for value in (3, 5):
            self.best_of.addItem(f"BO{value}", f"bo{value}")
        top.addWidget(self.best_of)
        top.addStretch()
        self.open_scoreboard = QPushButton("Scoreboardプレビュー")
        self.copy_scoreboard = QPushButton("OBS URLをコピー")
        top.addWidget(self.open_scoreboard)
        top.addWidget(self.copy_scoreboard)
        layout.addLayout(top)

        tabs = QTabWidget()
        match_tab = QWidget()
        match_layout = QVBoxLayout(match_tab)
        player_grid = QGridLayout()
        headers = ["", "Prefix・チーム", "選手名", "Rep", "Twitter", "国", "スコア"]
        for col, header in enumerate(headers):
            player_grid.addWidget(QLabel(header), 0, col)
        for side in (1, 2):
            player_grid.addWidget(QLabel(f"P{side}"), side, 0)
            for col, stem in enumerate(("pTeam", "pName", "pRep", "pTwitter", "pCountry"), start=1):
                edit = QLineEdit()
                key = f"{stem}{side}"
                self.fields[key] = edit
                player_grid.addWidget(edit, side, col)
            score = QSpinBox()
            score.setRange(0, 2)
            score.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
            score.setMinimumWidth(72)
            score.setMinimumHeight(34)
            self.fields[f"pScore{side}"] = score
            player_grid.addWidget(score, side, 6)
        match_layout.addLayout(player_grid)

        buttons = QHBoxLayout()
        for label, handler in (
            ("P1を消去", lambda: self.clear_player(1)),
            ("P2を消去", lambda: self.clear_player(2)),
            ("スコアを0に戻す", self.reset_scores),
            ("P1・P2を入れ替える", self.swap_players),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        match_layout.addLayout(buttons)

        form = QFormLayout()
        self.stage = QLineEdit()
        self.stream_url = QLineEdit()
        form.addRow("試合位置", self.stage)
        form.addRow("配信URL", self.stream_url)
        match_layout.addLayout(form)

        presentation = QGroupBox("新デザインの表示切り替え")
        presentation_row = QHBoxLayout(presentation)
        self.auto_scene_button = QPushButton("自動切り替え")
        self.in_game_button = QPushButton("試合中に固定")
        self.between_game_button = QPushButton("試合間に固定")
        self.presentation_status = QLabel()
        for button in (self.auto_scene_button, self.in_game_button, self.between_game_button):
            presentation_row.addWidget(button)
        presentation_row.addWidget(self.presentation_status)
        presentation_row.addStretch()
        match_layout.addWidget(presentation)

        action_row = QHBoxLayout()
        self.save_button = QPushButton("スコアボードへ反映")
        self.save_button.setObjectName("primary")
        self.sponsors_button = QPushButton("表示を再更新")
        self.sponsors_button.setToolTip(
            "同じ入力内容のまま、OBS Browser Sourceの更新アニメーションを再実行します。"
        )
        self.tweet_button = QPushButton("反映して投稿確認")
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.sponsors_button)
        action_row.addWidget(self.tweet_button)
        action_row.addStretch()
        match_layout.addLayout(action_row)
        tabs.addTab(match_tab, "試合")

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
        tabs.addTab(l3rd_tab, "補助テキスト")
        layout.addWidget(tabs)

        self.status = QLabel()
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)

        for widget in list(self.fields.values()) + [self.stage, self.stream_url]:
            if isinstance(widget, QLineEdit):
                widget.textEdited.connect(self.mark_dirty)
            else:
                widget.valueChanged.connect(self.mark_dirty)
        self.best_of.currentIndexChanged.connect(self.on_best_of_changed)
        for side in (1, 2):
            self.fields[f"pScore{side}"].valueChanged.connect(
                lambda _value, changed_side=side: self.on_score_changed(changed_side)
            )
        self.save_button.clicked.connect(self.save)
        self.sponsors_button.clicked.connect(self.show_sponsors)
        self.tweet_button.clicked.connect(self.open_tweet_dialog)
        self.auto_scene_button.clicked.connect(self.enable_auto_scene)
        self.in_game_button.clicked.connect(lambda: self.fix_scene("in_game"))
        self.between_game_button.clicked.connect(lambda: self.fix_scene("between_game"))
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
            self.update_score_ranges()
        finally:
            self._loading = False
        self._dirty = False
        self._last_mtime = self.store.path.stat().st_mtime if self.store.path.exists() else 0.0
        self.status.setText(f"Scoreboard URL: {self.scoreboard_url}")
        self.update_presentation_status()

    def update_score_ranges(self) -> None:
        wins_needed, _ = score_limits(str(self.best_of.currentData() or "bo3"))
        for side in (1, 2):
            self.fields[f"pScore{side}"].setMaximum(wins_needed)

    def on_best_of_changed(self, *args) -> None:
        self.update_score_ranges()
        self.on_score_changed(1)
        self.mark_dirty()

    def on_score_changed(self, changed_side: int) -> None:
        if self._loading:
            return
        _, total_limit = score_limits(str(self.best_of.currentData() or "bo3"))
        left = self.fields["pScore1"].value()
        right = self.fields["pScore2"].value()
        if left + right > total_limit:
            widget = self.fields[f"pScore{changed_side}"]
            other = right if changed_side == 1 else left
            self._loading = True
            widget.setValue(max(0, total_limit - other))
            self._loading = False
            self.status.setText(f"合計スコアは{total_limit}までです。")

    def update_presentation_status(self) -> None:
        if self.state_store is None:
            self.presentation_status.setText("表示状態を利用できません")
            return
        state = self.state_store.read()
        mode = "自動" if state.get("mode") == "automatic" else "固定"
        scene = "試合間" if state.get("scene") == "between_game" else "試合中"
        self.presentation_status.setText(f"現在: {mode} / {scene}")

    def enable_auto_scene(self) -> None:
        if self.state_store is not None:
            self.state_store.set_presentation(mode="automatic")
            self.update_presentation_status()

    def fix_scene(self, scene: str) -> None:
        if self.state_store is not None:
            self.state_store.set_presentation(scene=scene, mode="manual")
            self.update_presentation_status()
        self.on_scene_change(scene)

    def poll_external_update(self) -> None:
        self.update_presentation_status()
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
        try:
            self.store.write(self.collect())
        except ScoreLimitError as exc:
            self.status.setText(str(exc))
            return
        self.load()
        self.status.setText("StreamControlを更新しました。")

    def clear_player(self, side: int) -> None:
        for stem in ("pTeam", "pName", "pRep", "pTwitter", "pCountry"):
            self.fields[f"{stem}{side}"].clear()
        self.fields[f"pScore{side}"].setValue(0)
        self.mark_dirty()

    def reset_scores(self) -> None:
        self.fields["pScore1"].setValue(0)
        self.fields["pScore2"].setValue(0)
        self.mark_dirty()

    def swap_players(self) -> None:
        for stem in ("pTeam", "pName", "pRep", "pTwitter", "pCountry", "pScore"):
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
        try:
            self.store.write_with_sponsors(payload)
        except ScoreLimitError as exc:
            self.status.setText(str(exc))
            return
        self.load()
        self.status.setText("スコアボードを更新しました。")

    def open_tweet_dialog(self) -> None:
        payload = self.collect()
        try:
            self.store.write(payload)
        except ScoreLimitError as exc:
            self.status.setText(str(exc))
            return
        self.load()
        TweetDialog(payload, self).exec()


class DetectorSignals(QObject):
    log = Signal(str)
    score_updated = Signal()
    layout_captured = Signal(str, object)
    layout_capture_failed = Signal(str, str)
    layout_applied = Signal(str, int)
    layout_apply_failed = Signal(str, str)


class DetectorPage(QWidget):
    score_updated = Signal()
    obs_settings_changed = Signal(object)
    presentation_test_requested = Signal(str)
    transition_test_requested = Signal(object)
    detector_state_changed = Signal(str)

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
        self._detector_failed = False
        self.signals = DetectorSignals()
        self.signals.log.connect(self.append_log)
        self.signals.score_updated.connect(self.score_updated.emit)
        self.signals.layout_captured.connect(self.finish_layout_capture)
        self.signals.layout_capture_failed.connect(self.finish_layout_capture_failure)
        self.signals.layout_applied.connect(self.finish_layout_test)
        self.signals.layout_apply_failed.connect(self.finish_layout_test_failure)
        self.operations = ObsLayoutOperations()
        self._operation_runner = LatestTaskRunner("obs-ui-worker")
        self.layout_profiles = {
            "in_game": dict(settings.obs_in_game_layout),
            "between_game": dict(settings.obs_between_game_layout),
        }

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 8, 8)
        layout.setSpacing(8)
        config_group = QGroupBox("OBS接続・映像監視")
        form = QFormLayout(config_group)
        self.host = QLineEdit(settings.obs_host)
        self.port = ScrollSafeSpinBox()
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
        form.addRow("監視対象ソース", self.source)
        form.addRow("", self.write_scores)
        layout.addWidget(config_group)

        layout_group = QGroupBox("OBSレイアウト設定")
        layout_form = QFormLayout(layout_group)
        self.layout_enabled = QCheckBox("デザイン切り替えと同時に映像位置も切り替える")
        self.layout_enabled.setChecked(settings.obs_layout_enabled)
        self.layout_scene = QLineEdit(settings.obs_layout_scene_name)
        self.layout_game_source = QLineEdit(settings.obs_game_source_name)
        self.layout_player1_source = QLineEdit(settings.obs_player1_source_name)
        self.layout_player2_source = QLineEdit(settings.obs_player2_source_name)
        layout_form.addRow("", self.layout_enabled)
        layout_form.addRow("対象シーン／グループ", self.layout_scene)
        layout_form.addRow("ゲーム画面ソース", self.layout_game_source)
        layout_form.addRow("P1カメラソース", self.layout_player1_source)
        layout_form.addRow("P2カメラソース", self.layout_player2_source)

        delay_group = QGroupBox("動画トランジション無効時の自動切り替え遅延（秒）")
        delay_form = QFormLayout(delay_group)
        self.in_game_framework_delay = self._delay_spinbox(
            settings.transition_in_game_framework_delay
        )
        self.in_game_layout_delay = self._delay_spinbox(
            settings.transition_in_game_layout_delay
        )
        self.between_game_framework_delay = self._delay_spinbox(
            settings.transition_between_game_framework_delay
        )
        self.between_game_layout_delay = self._delay_spinbox(
            settings.transition_between_game_layout_delay
        )
        delay_form.addRow("試合間 → 試合中：フレーム", self.in_game_framework_delay)
        delay_form.addRow("試合間 → 試合中：OBS配置", self.in_game_layout_delay)
        delay_form.addRow("試合中 → 試合間：フレーム", self.between_game_framework_delay)
        delay_form.addRow("試合中 → 試合間：OBS配置", self.between_game_layout_delay)
        layout_form.addRow(delay_group)

        transition_group = QGroupBox("画面トランジション")
        transition_form = QFormLayout(transition_group)
        self.transition_enabled = QCheckBox("検知後にOBSでトランジション動画を再生する")
        self.transition_enabled.setChecked(settings.obs_transition_enabled)
        self.transition_vs_enabled = QCheckBox("VS検知後に再生")
        self.transition_vs_enabled.setChecked(settings.obs_transition_vs_enabled)
        self.transition_game_set_enabled = QCheckBox("GAME SET検知後に再生")
        self.transition_game_set_enabled.setChecked(settings.obs_transition_game_set_enabled)
        event_checks = QWidget()
        event_checks_layout = QHBoxLayout(event_checks)
        event_checks_layout.setContentsMargins(0, 0, 0, 0)
        event_checks_layout.addWidget(self.transition_vs_enabled)
        event_checks_layout.addWidget(self.transition_game_set_enabled)
        event_checks_layout.addStretch()
        self.transition_source = QLineEdit(settings.obs_transition_source_name)
        self.transition_url = QLineEdit(
            f"http://127.0.0.1:{settings.scoreboard_port}/transition.html"
        )
        self.transition_url.setReadOnly(True)
        self.transition_url.setCursorPosition(0)
        self.copy_transition_url = QPushButton("URLをコピー")
        url_row = QWidget()
        url_row_layout = QHBoxLayout(url_row)
        url_row_layout.setContentsMargins(0, 0, 0, 0)
        url_row_layout.addWidget(self.transition_url, 1)
        url_row_layout.addWidget(self.copy_transition_url)
        self.transition_switch_delay = self._delay_spinbox(
            settings.obs_transition_switch_delay
        )
        self.transition_test = QPushButton("Browser Sourceで再生テスト")
        self.transition_help = QLabel(
            "このURLをOBS Browser Sourceへ登録し、全画面・最前面に配置してください。"
            "待機中は透明です。再生開始を確認してから配置とデザインを切り替えます。"
        )
        self.transition_help.setWordWrap(True)
        self.transition_help.setStyleSheet("color: #475569;")
        transition_form.addRow("", self.transition_enabled)
        transition_form.addRow("再生する検知", event_checks)
        transition_form.addRow("Transition Browser Source名", self.transition_source)
        transition_form.addRow("OBS Browser Source URL", url_row)
        transition_form.addRow("配置・デザイン切替時点", self.transition_switch_delay)
        transition_form.addRow("", self.transition_test)
        transition_form.addRow("", self.transition_help)
        layout_form.addRow(transition_group)

        cards = QWidget()
        cards_layout = QHBoxLayout(cards)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)
        self.layout_previews: dict[str, LayoutPreview] = {}
        self.layout_status_labels: dict[str, QLabel] = {}
        self.layout_capture_buttons: dict[str, QPushButton] = {}
        self.layout_test_buttons: dict[str, QPushButton] = {}
        for scene, label in (("in_game", "試合中"), ("between_game", "試合間")):
            card = QGroupBox(f"{label}レイアウト")
            card_layout = QVBoxLayout(card)
            preview = LayoutPreview()
            preview.set_profile(self.layout_profiles[scene])
            status = QLabel()
            status.setStyleSheet("color: #475569;")
            capture_button = QPushButton("現在のOBS配置で上書き保存")
            test_button = QPushButton("配置とScoreboardをテスト")
            test_button.setObjectName("primary")
            card_layout.addWidget(preview)
            card_layout.addWidget(status)
            card_layout.addWidget(capture_button)
            card_layout.addWidget(test_button)
            cards_layout.addWidget(card, 1)
            self.layout_previews[scene] = preview
            self.layout_status_labels[scene] = status
            self.layout_capture_buttons[scene] = capture_button
            self.layout_test_buttons[scene] = test_button
            capture_button.clicked.connect(
                lambda _checked=False, selected=scene: self.capture_layout(selected)
            )
            test_button.clicked.connect(
                lambda _checked=False, selected=scene: self.test_layout(selected)
            )
            self.update_layout_preview(scene)
        self.capture_in_game = self.layout_capture_buttons["in_game"]
        self.capture_between = self.layout_capture_buttons["between_game"]
        self.test_in_game = self.layout_test_buttons["in_game"]
        self.test_between = self.layout_test_buttons["between_game"]
        layout_form.addRow(cards)

        self.save_obs_settings_button = QPushButton("OBS設定を保存")
        layout_form.addRow("", self.save_obs_settings_button)
        layout.addWidget(layout_group)

        controls = QHBoxLayout()
        self.start_button = QPushButton("映像監視を開始")
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
        self.log.setMinimumHeight(180)
        self.log.document().setMaximumBlockCount(1000)
        layout.addWidget(self.log, 1)
        self.start_button.clicked.connect(self.start_detector)
        self.stop_button.clicked.connect(self.stop_detector)
        self.save_obs_settings_button.clicked.connect(self.persist_obs_settings)
        self.copy_transition_url.clicked.connect(self.copy_transition_browser_url)
        self.transition_test.clicked.connect(self.test_transition_video)
        self.transition_enabled.toggled.connect(self.update_transition_controls)
        self.update_transition_controls(self.transition_enabled.isChecked())
        self.scroll_area.setWidget(content)
        outer_layout.addWidget(self.scroll_area, 1)

    @staticmethod
    def _delay_spinbox(value: float) -> QDoubleSpinBox:
        control = ScrollSafeDoubleSpinBox()
        control.setRange(0.0, 60.0)
        control.setDecimals(2)
        control.setSingleStep(0.1)
        control.setSuffix(" 秒")
        control.setValue(max(0.0, float(value)))
        return control

    def set_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def obs_settings_payload(self) -> dict:
        return {
            "obs_host": self.host.text().strip() or "localhost",
            "obs_port": self.port.value(),
            "obs_password": self.password.text(),
            "obs_source_name": self.source.text().strip(),
            "detector_write_scores": self.write_scores.isChecked(),
            "obs_layout_enabled": self.layout_enabled.isChecked(),
            "obs_layout_scene_name": self.layout_scene.text().strip(),
            "obs_game_source_name": self.layout_game_source.text().strip(),
            "obs_player1_source_name": self.layout_player1_source.text().strip(),
            "obs_player2_source_name": self.layout_player2_source.text().strip(),
            "obs_in_game_layout": dict(self.layout_profiles["in_game"]),
            "obs_between_game_layout": dict(self.layout_profiles["between_game"]),
            "transition_in_game_framework_delay": self.in_game_framework_delay.value(),
            "transition_in_game_layout_delay": self.in_game_layout_delay.value(),
            "transition_between_game_framework_delay": self.between_game_framework_delay.value(),
            "transition_between_game_layout_delay": self.between_game_layout_delay.value(),
            "obs_transition_enabled": self.transition_enabled.isChecked(),
            "obs_transition_vs_enabled": self.transition_vs_enabled.isChecked(),
            "obs_transition_game_set_enabled": self.transition_game_set_enabled.isChecked(),
            "obs_transition_source_name": self.transition_source.text().strip(),
            "obs_transition_switch_delay": self.transition_switch_delay.value(),
        }

    def update_transition_controls(self, enabled: bool) -> None:
        for control in (
            self.transition_vs_enabled,
            self.transition_game_set_enabled,
            self.transition_source,
            self.transition_switch_delay,
        ):
            control.setEnabled(enabled)
        self.transition_test.setEnabled(True)

    def set_transition_url(self, url: str) -> None:
        self.transition_url.setText(url)
        self.transition_url.setCursorPosition(0)

    def copy_transition_browser_url(self) -> None:
        QApplication.clipboard().setText(self.transition_url.text())
        self.append_log("Transition Browser Source URLをコピーしました。")

    def test_transition_video(self) -> None:
        settings = self._layout_settings_snapshot()
        self.transition_test_requested.emit(settings)
        self.append_log("OBSトランジションの再生テストを要求しました。")

    def persist_obs_settings(self) -> None:
        self.obs_settings_changed.emit(self.obs_settings_payload())
        self.append_log("OBS接続・レイアウト設定を保存しました。")

    def _layout_settings_snapshot(self) -> AppSettings:
        snapshot = replace(self.settings)
        for key, value in self.obs_settings_payload().items():
            setattr(snapshot, key, value)
        return snapshot

    def capture_layout(self, scene: str) -> None:
        if self.layout_profiles.get(scene):
            label = "試合間" if scene == "between_game" else "試合中"
            answer = QMessageBox.warning(
                self,
                "レイアウトの上書き",
                f"現在のOBS配置で「{label}レイアウト」を上書きしますか？\n\n"
                "現在保存されている配置は置き換えられます。",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return
        settings = self._layout_settings_snapshot()
        label = "試合間" if scene == "between_game" else "試合中"
        self._set_layout_operation_busy(True)
        self.layout_capture_buttons[scene].setText("OBSから取得中…")
        self.append_log(f"OBSから{label}レイアウトを取得しています…")
        self._operation_runner.submit(
            lambda: self.operations.capture(settings),
            lambda profile: self.signals.layout_captured.emit(scene, profile),
            lambda message: self.signals.layout_capture_failed.emit(scene, message),
        )

    def finish_layout_capture(self, scene: str, profile: dict) -> None:
        self.layout_profiles[scene] = dict(profile)
        label = "試合間" if scene == "between_game" else "試合中"
        self._set_layout_operation_busy(False)
        self.update_layout_preview(scene)
        self.persist_obs_settings()
        self.append_log(f"{label}レイアウトを保存しました（{len(profile)}ソース）。")

    def finish_layout_capture_failure(self, scene: str, message: str) -> None:
        self._set_layout_operation_busy(False)
        self.update_layout_preview(scene)
        self.append_log(f"OBSレイアウト取得失敗: {message}")

    def update_layout_preview(self, scene: str) -> None:
        profile = self.layout_profiles.get(scene, {})
        self.layout_previews[scene].set_profile(profile)
        if profile:
            self.layout_status_labels[scene].setText(
                f"保存済み：{len(profile)}ソース（位置・大きさ・レイヤー順）"
            )
            self.layout_capture_buttons[scene].setText("現在のOBS配置で上書き保存")
            self.layout_test_buttons[scene].setText("配置とScoreboardをテスト")
            self.layout_test_buttons[scene].setEnabled(True)
        else:
            self.layout_status_labels[scene].setText("未保存")
            self.layout_capture_buttons[scene].setText("現在のOBS配置を保存")
            self.layout_test_buttons[scene].setText("配置とScoreboardをテスト")
            self.layout_test_buttons[scene].setEnabled(False)

    def test_layout(self, scene: str) -> None:
        settings = self._layout_settings_snapshot()
        profile = self.layout_profiles[scene]
        label = "試合間" if scene == "between_game" else "試合中"
        if not profile:
            self.append_log(f"{label}レイアウトはまだ保存されていません。")
            return
        self.presentation_test_requested.emit(scene)
        self.append_log(f"Scoreboardを{label}表示へ切り替えました。")
        self._set_layout_operation_busy(True)
        self.layout_test_buttons[scene].setText("OBSへ適用中…")
        self._operation_runner.submit(
            lambda: self.operations.apply(settings, scene, profile),
            lambda result: self.signals.layout_applied.emit(result.scene, result.count),
            lambda message: self.signals.layout_apply_failed.emit(scene, message),
        )

    def finish_layout_test(self, scene: str, count: int) -> None:
        self._set_layout_operation_busy(False)
        label = "試合間" if scene == "between_game" else "試合中"
        self.append_log(f"OBSへ{label}レイアウトを適用しました（{count}ソース）。")

    def finish_layout_test_failure(self, scene: str, message: str) -> None:
        self._set_layout_operation_busy(False)
        label = "試合間" if scene == "between_game" else "試合中"
        self.append_log(f"OBS {label}レイアウト適用失敗: {message}")

    def _set_layout_operation_busy(self, busy: bool) -> None:
        for scene in ("in_game", "between_game"):
            self.layout_capture_buttons[scene].setEnabled(not busy)
            self.layout_test_buttons[scene].setEnabled(
                not busy and bool(self.layout_profiles.get(scene))
            )
            if not busy:
                self.update_layout_preview(scene)

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        if line.startswith("OBS接続:"):
            self._detector_failed = False
            self.detector_state_changed.emit("running")
        elif line.startswith("Detector停止:"):
            self._detector_failed = True
            self.detector_state_changed.emit("error")
        if line.startswith("Detectorを停止") or line.startswith("Detector停止"):
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            if line.startswith("Detectorを停止") and not self._detector_failed:
                self.detector_state_changed.emit("stopped")

    def start_detector(self) -> None:
        self._detector_failed = False
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
        self.detector_state_changed.emit("starting")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.append_log("Detectorを開始しています…")

    def stop_detector(self) -> None:
        if self.service:
            self.service.stop()
            self.detector_state_changed.emit("stopping")
        self.stop_button.setEnabled(False)

    def close_operations(self) -> None:
        self._operation_runner.close()


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
        self.scoreboard_port = ScrollSafeSpinBox()
        self.scoreboard_port.setRange(1024, 65535)
        self.scoreboard_port.setMinimumHeight(32)
        local_form.addRow("Scoreboard HTTP Port", self.scoreboard_port)
        self.scoreboard_design = QComboBox()
        self.scoreboard_design.addItem("旧デザイン", "legacy")
        self.scoreboard_design.addItem("新デザイン", "aya")
        self.player_camera = QComboBox()
        self.player_camera.addItem("OFF（カメラ枠なし）", False)
        self.player_camera.addItem("ON（カメラ枠あり・自動切り替え）", True)
        local_form.addRow("スコアボードデザイン", self.scoreboard_design)
        local_form.addRow("選手カメラ", self.player_camera)
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
        self.scoreboard_design.setCurrentIndex(
            max(0, self.scoreboard_design.findData(settings.scoreboard_design))
        )
        self.player_camera.setCurrentIndex(
            max(0, self.player_camera.findData(settings.player_camera_enabled))
        )
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
            scoreboard_design=str(self.scoreboard_design.currentData()),
            player_camera_enabled=bool(self.player_camera.currentData()),
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
        self.detector_event_bus = EventBus()
        self.event_bus = EventBus()
        self.scoreboard_state = ScoreboardStateStore(paths.scoreboard_state_file)
        self.scoreboard_state.configure(
            self.settings.scoreboard_design,
            self.settings.player_camera_enabled,
        )
        self.transition_state = TransitionStateStore()
        self.obs_transition = BrowserTransitionController(
            self.detector_event_bus,
            self.event_bus,
            self.settings,
            self.scoreboard_state,
            self.transition_state,
        )
        self.automation = AutomationEngine(
            self.event_bus,
            paths.automation_rules_file,
            self.scoreboard_state,
            self.settings,
        )
        self.obs_layout = ObsLayoutController(
            self.event_bus,
            self.settings,
            self.scoreboard_state,
        )
        self.server = ScoreboardServer(
            paths.scoreboard_assets,
            self.store,
            self.settings.scoreboard_port,
            self.scoreboard_state,
            self.transition_state,
        )
        self._server_running = False
        try:
            self.server.start()
            self._server_running = True
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
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.broadcast_status = BroadcastStatusStrip()
        root_layout.addWidget(self.broadcast_status)
        workspace = QWidget()
        layout = QHBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(170)
        self.navigation.addItems(["大会・試合", "配信操作", "OBS・自動化", "設定"])
        self.stack = QStackedWidget()

        self.startgg_page = StartGGPage(self.settings, self.store)
        self.streamcontrol_page = StreamControlPage(
            self.store,
            self.server.scoreboard_url,
            self.scoreboard_state,
            self.apply_manual_scene,
        )
        self.detector_page = DetectorPage(self.settings, paths, self.detector_event_bus)
        self.obs_layout.set_log_callback(self.detector_page.signals.log.emit)
        self.obs_transition.set_log_callback(self.detector_page.signals.log.emit)
        self.settings_page = SettingsPage(self.settings)
        for page in (self.startgg_page, self.streamcontrol_page, self.detector_page, self.settings_page):
            self.stack.addWidget(page)
        layout.addWidget(self.navigation)
        layout.addWidget(self.stack, 1)
        root_layout.addWidget(workspace, 1)
        self.setCentralWidget(central)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.startgg_page.streamcontrol_updated.connect(self.streamcontrol_page.load)
        self.startgg_page.phase_rounds_loaded.connect(self.update_phase_catalog)
        self.detector_page.score_updated.connect(self.streamcontrol_page.load)
        self.detector_page.obs_settings_changed.connect(self.save_obs_settings)
        self.detector_page.presentation_test_requested.connect(self.test_scoreboard_presentation)
        self.detector_page.transition_test_requested.connect(self.test_transition_video)
        self.detector_page.detector_state_changed.connect(self.broadcast_status.set_detector)
        self.settings_page.saved.connect(self.save_settings)
        self.statusBar().showMessage(f"OBS Browser Source: {self.server.scoreboard_url}")
        self.broadcast_status.set_detector("stopped")
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(750)
        self._status_timer.timeout.connect(self.refresh_broadcast_status)
        self._status_timer.start()
        self.refresh_broadcast_status()

    def refresh_broadcast_status(self) -> None:
        self.broadcast_status.set_server(self._server_running, self.server.scoreboard_url)
        state = self.scoreboard_state.read()
        self.broadcast_status.set_presentation(
            str(state.get("mode", "automatic")),
            str(state.get("scene", "in_game")),
        )
        try:
            self.broadcast_status.set_match(self.store.read())
        except (OSError, ValueError):
            self.broadcast_status.match.setText("試合: データを読み込めません")

    def apply_manual_scene(self, scene: str) -> None:
        self.automation.cancel_pending_transition()
        self.obs_layout.apply_async(scene)

    def test_scoreboard_presentation(self, scene: str) -> None:
        """Switch the scoreboard immediately when testing a saved OBS layout."""
        self.automation.cancel_pending_transition()
        self.scoreboard_state.set_presentation(scene=scene)
        self.streamcontrol_page.update_presentation_status()

    def test_transition_video(self, settings: AppSettings) -> None:
        self.obs_transition.preview_async(settings)

    def update_phase_catalog(self, catalog: dict[str, dict]) -> None:
        self.settings.phase_round_options = dict(catalog)
        self.settings_page.update_phase_catalog(catalog)

    def save_settings(self, new_settings: AppSettings) -> None:
        # OBS connection and layout fields live on the OBS video management page.
        for key, value in self.detector_page.obs_settings_payload().items():
            setattr(new_settings, key, value)
        old_port = self.settings.scoreboard_port
        self.settings = new_settings
        self.settings_store.save(new_settings)
        self.startgg_page.set_settings(new_settings)
        self.detector_page.set_settings(new_settings)
        self.obs_layout.update_settings(new_settings)
        self.obs_transition.update_settings(new_settings)
        self.automation.update_settings(new_settings)
        self.scoreboard_state.configure(
            new_settings.scoreboard_design,
            new_settings.player_camera_enabled,
        )
        self.streamcontrol_page.update_presentation_status()

        if old_port != new_settings.scoreboard_port:
            self.server.stop()
            self._server_running = False
            self.server = ScoreboardServer(
                self.paths.scoreboard_assets,
                self.store,
                new_settings.scoreboard_port,
                self.scoreboard_state,
                self.transition_state,
            )
            try:
                self.server.start()
                self._server_running = True
            except OSError as exc:
                QMessageBox.warning(self, "設定", f"新しいポートでScoreboardを開始できません: {exc}")
            self.streamcontrol_page.set_scoreboard_url(self.server.scoreboard_url)
            self.detector_page.set_transition_url(self.server.transition_url)
        self.statusBar().showMessage(f"設定を保存しました。OBS Browser Source: {self.server.scoreboard_url}", 8000)

    def save_obs_settings(self, values: dict) -> None:
        for key, value in values.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.settings_store.save(self.settings)
        self.detector_page.set_settings(self.settings)
        self.obs_layout.update_settings(self.settings)
        self.obs_transition.update_settings(self.settings)
        self.automation.update_settings(self.settings)
        self.statusBar().showMessage("OBS接続・レイアウト設定を保存しました。", 6000)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._status_timer.stop()
        self.detector_page.stop_detector()
        self.startgg_page.close_operations()
        self.detector_page.close_operations()
        self.server.stop()
        self._server_running = False
        self.obs_transition.close()
        self.automation.close()
        self.obs_layout.close()
        event.accept()
