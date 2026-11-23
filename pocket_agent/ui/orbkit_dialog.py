"""Theme-aware dialog for browsing, previewing, importing, and managing Orbkit orbs."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import threading

from PyQt6.QtCore import QObject, QPointF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pocket_agent.engine.orb_shaders import OrbGlRenderer
from pocket_agent.engine.orbkit import (
    ORBKIT_PLAYGROUND_URL,
    ORBKIT_REPO_URL,
    CustomOrbDefinition,
    OrbkitCatalogEntry,
    delete_custom_orb,
    fetch_orbkit_catalog,
    fetch_orbkit_orb,
    load_custom_orbs,
    parse_orbkit_registry_json,
    parse_orbkit_tsx,
    resolve_orbkit_input,
    save_custom_orb,
    unique_orb_id,
)
from pocket_agent.engine.orbs import OrbPlayer
from pocket_agent.ui.theme import (
    FONT_MONO,
    FONT_UI,
    apply_windows_backdrop,
    get_palette,
    lucide_icon,
)

_PREVIEW_ORB_ID = "__orbkit_preview__"


class _FetchWorker(QObject):
    """Marshals background HTTP fetches back onto the Qt GUI thread."""

    catalog_loaded = pyqtSignal(list)
    catalog_failed = pyqtSignal(str)
    orb_loaded = pyqtSignal(object)
    orb_failed = pyqtSignal(str)

    def fetch_catalog(self) -> None:
        def _run() -> None:
            try:
                entries = fetch_orbkit_catalog()
                self.catalog_loaded.emit(entries)
            except Exception as exc:
                self.catalog_failed.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def resolve_input(self, raw_text: str) -> None:
        def _run() -> None:
            try:
                orb = resolve_orbkit_input(raw_text)
                self.orb_loaded.emit(orb)
            except Exception as exc:
                self.orb_failed.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def fetch_slug(self, slug: str) -> None:
        def _run() -> None:
            try:
                orb = fetch_orbkit_orb(slug)
                self.orb_loaded.emit(orb)
            except Exception as exc:
                self.orb_failed.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()


class OrbPreviewWidget(QWidget):
    """Live GPU OpenGL preview canvas for a candidate or installed Orbkit orb."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(216, 216)
        self.setMouseTracking(True)
        self._player: OrbPlayer | None = None
        self._state = "idle"
        self._hovered = False
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._on_tick)

    def set_orb(self, orb: CustomOrbDefinition | None) -> str:
        """Compile and display `orb` under the preview ID; return compile error or ''."""
        if orb is None:
            self._player = None
            self._timer.stop()
            OrbGlRenderer.instance().unregister_shader(_PREVIEW_ORB_ID)
            self.update()
            return ""

        preview_orb = replace(orb, id=_PREVIEW_ORB_ID)
        err = OrbGlRenderer.instance().compile_shader(
            _PREVIEW_ORB_ID,
            preview_orb.build_fragment_shader(),
        )
        if err:
            self._player = None
            self._timer.stop()
            self.update()
            return err

        self._player = OrbPlayer(_PREVIEW_ORB_ID, custom_orb=preview_orb)
        self._player.set_orb_state(self._state)
        self._player.update(33, is_hovered=self._hovered)
        if not self._timer.isActive():
            self._timer.start()
        self.update()
        return ""

    def set_state(self, state: str) -> None:
        self._state = state
        if self._player is not None:
            self._player.set_orb_state(state)
            self.update()

    def stop(self) -> None:
        self._timer.stop()
        self._player = None
        OrbGlRenderer.instance().unregister_shader(_PREVIEW_ORB_ID)

    def _on_tick(self) -> None:
        if self._player is None:
            return
        if self._player.update(33, is_hovered=self._hovered):
            self.update()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        if hasattr(event, "position") and self._player is not None:
            self._update_cursor(event.position())

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        if self._player is not None:
            self._player.set_hover_cursor(0.0, 0.0)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._player is not None:
            self._update_cursor(event.position())

    def _update_cursor(self, pos: QPointF) -> None:
        if self._player is None:
            return
        nx = (pos.x() - self.width() / 2.0) / max(1.0, self.width() / 2.0)
        ny = (pos.y() - self.height() / 2.0) / max(1.0, self.height() / 2.0)
        self._player.set_hover_cursor(nx, ny)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        pal = get_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(1.0, 1.0, self.width() - 2.0, self.height() - 2.0, 12.0, 12.0)
        painter.fillPath(path, QColor(pal.bg_secondary))
        painter.setPen(QColor(pal.border))
        painter.drawPath(path)

        if self._player is not None:
            self._player.paint(painter, self.rect().adjusted(6, 6, -6, -6))
        else:
            painter.setPen(QColor(pal.text_muted))
            painter.drawText(
                self.rect().adjusted(14, 14, -14, -14),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "Preview will appear here",
            )


class OrbkitManagerDialog(QDialog):
    """Browse, customize, import, preview, and manage Orbkit (`zzzzshawn/orbkit`) orbs."""

    orbs_changed = pyqtSignal()
    orb_selected = pyqtSignal(str)

    def __init__(
        self,
        custom_orbs_dir: Path,
        current_pet_id: str = "",
        auto_fetch_catalog: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._orbs_dir = custom_orbs_dir
        self._current_pet_id = current_pet_id
        self._candidate: CustomOrbDefinition | None = None
        self._installed: list[CustomOrbDefinition] = []
        self._catalog: list[OrbkitCatalogEntry] = []
        self._worker = _FetchWorker(self)

        self.setWindowTitle("Orbkit Orbs")
        self.resize(760, 560)
        self._build_ui()
        self._wire_signals()
        self._apply_theme()
        self._refresh_installed()

        if auto_fetch_catalog:
            self._catalog_combo.setEnabled(False)
            self._load_catalog_btn.setEnabled(False)
            self._catalog_status_lbl.setText("Loading Orbkit gallery...")
            self._worker.fetch_catalog()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        apply_windows_backdrop(self, is_dark=get_palette().is_dark, blur=False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._preview.stop()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._preview.stop()
        super().done(result)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # Header
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._title_lbl = QLabel("Orbkit Orbs (zzzzshawn/orbkit)", self)
        self._subtitle_lbl = QLabel(
            "Customize an existing orb in the Orbkit Playground or author a new `.tsx` orb in the repo, "
            "then paste the JSX snippet, URL, slug, or file below to add it to Pocket Agent.",
            self,
        )
        self._subtitle_lbl.setWordWrap(True)
        title_col.addWidget(self._title_lbl)
        title_col.addWidget(self._subtitle_lbl)
        header_row.addLayout(title_col, 1)

        self._open_playground_btn = QPushButton("Open Playground", self)
        self._open_playground_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_repo_btn = QPushButton("Open Orbkit Repo", self)
        self._open_repo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_row.addWidget(self._open_playground_btn)
        header_row.addWidget(self._open_repo_btn)
        root.addLayout(header_row)

        # Main two-column split
        body = QHBoxLayout()
        body.setSpacing(12)

        # ---- Left Column: Gallery + Import + Installed ----
        left_col = QVBoxLayout()
        left_col.setSpacing(10)

        # Gallery card
        self._gallery_card = QFrame(self)
        self._gallery_card.setObjectName("OrbCard")
        g_lay = QVBoxLayout(self._gallery_card)
        g_lay.setContentsMargins(12, 10, 12, 10)
        g_lay.setSpacing(6)
        self._gallery_title = QLabel("Orbkit Gallery", self._gallery_card)
        g_lay.addWidget(self._gallery_title)

        g_row = QHBoxLayout()
        g_row.setSpacing(8)
        self._catalog_combo = QComboBox(self._gallery_card)
        g_row.addWidget(self._catalog_combo, 1)
        self._load_catalog_btn = QPushButton("Load Selected", self._gallery_card)
        self._load_catalog_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        g_row.addWidget(self._load_catalog_btn)
        g_lay.addLayout(g_row)

        self._catalog_status_lbl = QLabel("Pick from Orbkit or paste custom code below.", self._gallery_card)
        self._catalog_status_lbl.setWordWrap(True)
        g_lay.addWidget(self._catalog_status_lbl)
        left_col.addWidget(self._gallery_card)

        # Paste / Import card
        self._import_card = QFrame(self)
        self._import_card.setObjectName("OrbCard")
        i_lay = QVBoxLayout(self._import_card)
        i_lay.setContentsMargins(12, 10, 12, 10)
        i_lay.setSpacing(8)
        self._import_title = QLabel("Paste Playground Snippet, TSX, URL, or Slug", self._import_card)
        i_lay.addWidget(self._import_title)

        self._source_input = QPlainTextEdit(self._import_card)
        self._source_input.setPlaceholderText(
            "Examples:\n"
            "• <Shdr32 size={420} state=\"idle\" wrapper=\"glass\" statePresets={{...}} />\n"
            "• shdr-11  or  npx shadcn@latest add zzzzshawn/orbkit/shdr-11\n"
            "• https://orbkit.zzzzshawn.cloud/playground?orb=shdr-32\n"
            "• Full OrbVariant `.tsx` or registry `.json` source"
        )
        self._source_input.setMinimumHeight(112)
        i_lay.addWidget(self._source_input)

        i_btn_row = QHBoxLayout()
        i_btn_row.setSpacing(8)
        self._preview_btn = QPushButton("Load & Preview", self._import_card)
        self._preview_btn.setObjectName("AccentButton")
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        i_btn_row.addWidget(self._preview_btn)

        self._browse_file_btn = QPushButton("Import File (.tsx / .json)...", self._import_card)
        self._browse_file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        i_btn_row.addWidget(self._browse_file_btn)
        i_btn_row.addStretch(1)
        i_lay.addLayout(i_btn_row)
        left_col.addWidget(self._import_card)

        # Installed custom orbs card
        self._installed_card = QFrame(self)
        self._installed_card.setObjectName("OrbCard")
        ins_lay = QVBoxLayout(self._installed_card)
        ins_lay.setContentsMargins(12, 10, 12, 10)
        ins_lay.setSpacing(6)
        self._installed_title = QLabel("Installed Custom Orbs", self._installed_card)
        ins_lay.addWidget(self._installed_title)

        self._installed_list = QListWidget(self._installed_card)
        self._installed_list.setMinimumHeight(105)
        ins_lay.addWidget(self._installed_list, 1)

        ins_btn_row = QHBoxLayout()
        ins_btn_row.setSpacing(8)
        self._use_installed_btn = QPushButton("Use Now", self._installed_card)
        self._use_installed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_installed_btn = QPushButton("Remove", self._installed_card)
        self._remove_installed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ins_btn_row.addWidget(self._use_installed_btn)
        ins_btn_row.addWidget(self._remove_installed_btn)
        ins_btn_row.addStretch(1)
        ins_lay.addLayout(ins_btn_row)
        left_col.addWidget(self._installed_card, 1)

        body.addLayout(left_col, 3)

        # ---- Right Column: Live Preview & Save ----
        self._preview_card = QFrame(self)
        self._preview_card.setObjectName("OrbCard")
        p_lay = QVBoxLayout(self._preview_card)
        p_lay.setContentsMargins(14, 12, 14, 12)
        p_lay.setSpacing(8)

        self._preview_title = QLabel("Live Preview", self._preview_card)
        p_lay.addWidget(self._preview_title)

        preview_wrap = QHBoxLayout()
        preview_wrap.addStretch(1)
        self._preview = OrbPreviewWidget(self._preview_card)
        preview_wrap.addWidget(self._preview)
        preview_wrap.addStretch(1)
        p_lay.addLayout(preview_wrap)

        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self._state_btns: dict[str, QPushButton] = {}
        for st in ("idle", "thinking", "speaking"):
            btn = QPushButton(st.capitalize(), self._preview_card)
            btn.setCheckable(True)
            btn.setChecked(st == "idle")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, s=st: self._set_preview_state(s))
            self._state_btns[st] = btn
            state_row.addWidget(btn)
        p_lay.addLayout(state_row)

        self._name_lbl = QLabel("Display Name", self._preview_card)
        p_lay.addWidget(self._name_lbl)
        self._name_edit = QLineEdit(self._preview_card)
        self._name_edit.setPlaceholderText("My Custom Orb")
        p_lay.addWidget(self._name_edit)

        self._meta_lbl = QLabel("", self._preview_card)
        self._meta_lbl.setWordWrap(True)
        p_lay.addWidget(self._meta_lbl)

        self._status_lbl = QLabel("", self._preview_card)
        self._status_lbl.setWordWrap(True)
        p_lay.addWidget(self._status_lbl)

        p_lay.addStretch(1)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self._add_use_btn = QPushButton("Add & Use Orb", self._preview_card)
        self._add_use_btn.setObjectName("AccentButton")
        self._add_use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn = QPushButton("Add to Library", self._preview_card)
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_row.addWidget(self._add_use_btn)
        save_row.addWidget(self._add_btn)
        p_lay.addLayout(save_row)

        body.addWidget(self._preview_card, 2)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        footer.addWidget(self._close_btn)
        root.addLayout(footer)

        self._set_candidate_buttons_enabled(False)

    def _wire_signals(self) -> None:
        self._open_playground_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(ORBKIT_PLAYGROUND_URL))
        )
        self._open_repo_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(ORBKIT_REPO_URL))
        )
        self._load_catalog_btn.clicked.connect(self._on_load_catalog_clicked)
        self._preview_btn.clicked.connect(self._on_preview_clicked)
        self._browse_file_btn.clicked.connect(self._on_browse_file)
        self._installed_list.currentItemChanged.connect(self._on_installed_selected)
        self._installed_list.itemDoubleClicked.connect(lambda _item: self._on_use_installed())
        self._use_installed_btn.clicked.connect(self._on_use_installed)
        self._remove_installed_btn.clicked.connect(self._on_remove_installed)
        self._add_use_btn.clicked.connect(lambda: self._save_candidate(use_now=True))
        self._add_btn.clicked.connect(lambda: self._save_candidate(use_now=False))
        self._close_btn.clicked.connect(self.accept)

        self._worker.catalog_loaded.connect(self._on_catalog_loaded)
        self._worker.catalog_failed.connect(self._on_catalog_failed)
        self._worker.orb_loaded.connect(self._on_orb_loaded)
        self._worker.orb_failed.connect(self._on_orb_failed)

    def _set_preview_state(self, state: str) -> None:
        for st, btn in self._state_btns.items():
            btn.setChecked(st == state)
        self._preview.set_state(state)

    def _set_candidate_buttons_enabled(self, enabled: bool) -> None:
        self._add_use_btn.setEnabled(enabled)
        self._add_btn.setEnabled(enabled)
        self._name_edit.setEnabled(enabled)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._preview_btn.setEnabled(not busy)
        self._browse_file_btn.setEnabled(not busy)
        self._load_catalog_btn.setEnabled(not busy and bool(self._catalog))
        if message:
            self._set_status(message, is_error=False)

    def _set_status(self, message: str, *, is_error: bool = False) -> None:
        pal = get_palette()
        col = pal.red if is_error else pal.text_muted
        self._status_lbl.setStyleSheet(
            f"color: {col}; font-size: 11px; font-family: {FONT_UI}; background: transparent;"
        )
        self._status_lbl.setText(message)

    def _on_catalog_loaded(self, entries: list[OrbkitCatalogEntry]) -> None:
        self._catalog = entries
        self._catalog_combo.clear()
        for entry in entries:
            label = f"{entry.title} — {entry.note}" if entry.note else entry.title
            self._catalog_combo.addItem(label, entry.slug)
        self._catalog_combo.setEnabled(bool(entries))
        self._load_catalog_btn.setEnabled(bool(entries))
        self._catalog_status_lbl.setText(
            f"{len(entries)} orbs ready from Orbkit. Select one or paste your customized Playground JSX below."
        )

    def _on_catalog_failed(self, err: str) -> None:
        self._catalog_combo.setEnabled(False)
        self._load_catalog_btn.setEnabled(False)
        self._catalog_status_lbl.setText(
            f"Gallery offline ({err}). Paste a `.tsx`/`.json` orb or import a local file below."
        )

    def _on_load_catalog_clicked(self) -> None:
        slug = self._catalog_combo.currentData()
        if not isinstance(slug, str) or not slug:
            return
        self._set_busy(True, f"Fetching {slug} from Orbkit...")
        self._worker.fetch_slug(slug)

    def _on_preview_clicked(self) -> None:
        raw = self._source_input.toPlainText().strip()
        if not raw:
            self._set_status("Paste a Playground snippet, TSX source, URL, or slug first.", is_error=True)
            return
        self._set_busy(True, "Loading and compiling orb shader...")
        self._worker.resolve_input(raw)

    def _on_browse_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Import Orbkit Orb File",
            str(Path.home()),
            "Orbkit Files (*.tsx *.json);;All Files (*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            content = path.read_text(encoding="utf-8")
            if path.suffix.lower() == ".json":
                orb = parse_orbkit_registry_json(json.loads(content), source_url=str(path))
            else:
                orb = parse_orbkit_tsx(content, fallback_slug=path.stem, source_url=str(path))
        except Exception as exc:
            self._set_status(f"Import failed: {exc}", is_error=True)
            return
        self._source_input.setPlainText(content)
        self._on_orb_loaded(orb)

    def _on_orb_loaded(self, orb: CustomOrbDefinition) -> None:
        self._set_busy(False)
        self._candidate = orb
        self._name_edit.setText(orb.name)

        meta_parts = [f"{len(orb.params)} params, {len(orb.colors)} colors"]
        if orb.wrapper != "none":
            meta_parts.append(f"wrapper: {orb.wrapper}")
        if orb.credit:
            author = orb.credit.get("handle") or orb.credit.get("author") or ""
            lic = orb.credit.get("license") or orb.license or "Non-commercial attribution"
            meta_parts.append(f"Shader by @{author} ({lic})")
        elif orb.license:
            meta_parts.append(f"License: {orb.license}")
        self._meta_lbl.setText(" · ".join(meta_parts))

        err = self._preview.set_orb(orb)
        if err:
            self._set_candidate_buttons_enabled(False)
            self._set_status(f"GLSL compile failed: {err}", is_error=True)
        else:
            self._set_candidate_buttons_enabled(True)
            self._set_status("Shader compiled and ready to add to Pocket Agent.", is_error=False)

    def _on_orb_failed(self, err: str) -> None:
        self._set_busy(False)
        self._set_candidate_buttons_enabled(False)
        self._set_status(f"Could not load orb: {err}", is_error=True)

    def _save_candidate(self, *, use_now: bool) -> None:
        if self._candidate is None:
            return
        name = self._name_edit.text().strip() or self._candidate.name
        existing_ids = {o.id for o in self._installed}
        new_id = unique_orb_id(name, existing_ids)
        to_save = replace(self._candidate, id=new_id, name=name)
        try:
            save_custom_orb(to_save, self._orbs_dir)
        except Exception as exc:
            self._set_status(f"Failed to save orb: {exc}", is_error=True)
            return

        self._refresh_installed(select_id=to_save.id)
        self.orbs_changed.emit()
        if use_now:
            self._current_pet_id = to_save.id
            self.orb_selected.emit(to_save.id)
            self._set_status(f"Added and activated '{to_save.name}'.", is_error=False)
            self.accept()
        else:
            self._set_status(f"Saved '{to_save.name}' to your Voice Orb menu.", is_error=False)

    def _refresh_installed(self, select_id: str = "") -> None:
        self._installed = load_custom_orbs(self._orbs_dir)
        self._installed_list.blockSignals(True)
        self._installed_list.clear()
        target_row = -1
        for idx, orb in enumerate(self._installed):
            suffix = " (active)" if orb.id == self._current_pet_id else ""
            item = QListWidgetItem(f"{orb.name}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, orb.id)
            self._installed_list.addItem(item)
            if select_id and orb.id == select_id:
                target_row = idx
            elif not select_id and orb.id == self._current_pet_id:
                target_row = idx
        if target_row >= 0:
            self._installed_list.setCurrentRow(target_row)
        self._installed_list.blockSignals(False)
        has_sel = self._installed_list.currentItem() is not None
        self._use_installed_btn.setEnabled(has_sel)
        self._remove_installed_btn.setEnabled(has_sel)

    def _selected_installed_orb(self) -> CustomOrbDefinition | None:
        item = self._installed_list.currentItem()
        if item is None:
            return None
        oid = item.data(Qt.ItemDataRole.UserRole)
        for orb in self._installed:
            if orb.id == oid:
                return orb
        return None

    def _on_installed_selected(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        has_sel = current is not None
        self._use_installed_btn.setEnabled(has_sel)
        self._remove_installed_btn.setEnabled(has_sel)
        orb = self._selected_installed_orb()
        if orb is not None:
            self._on_orb_loaded(orb)

    def _on_use_installed(self) -> None:
        orb = self._selected_installed_orb()
        if orb is None:
            return
        self._current_pet_id = orb.id
        self.orb_selected.emit(orb.id)
        self._refresh_installed(select_id=orb.id)
        self.accept()

    def _on_remove_installed(self) -> None:
        orb = self._selected_installed_orb()
        if orb is None:
            return
        if delete_custom_orb(orb.id, self._orbs_dir):
            OrbGlRenderer.instance().unregister_shader(orb.id)
            self._refresh_installed()
            self.orbs_changed.emit()
            self._set_status(f"Removed '{orb.name}'.", is_error=False)

    def _apply_theme(self) -> None:
        pal = get_palette()
        self.setStyleSheet(
            f"QDialog {{ background: {pal.bg_primary}; color: {pal.text_normal}; font-family: {FONT_UI}; font-size: 12px; }}"
            f"#OrbCard {{ background: {pal.bg_card}; border: 1px solid {pal.border_subtle}; border-radius: 10px; }}"
            f"QLineEdit, QPlainTextEdit, QComboBox, QListWidget {{"
            f"  background: {pal.bg_secondary}; color: {pal.text_normal}; border: 1px solid {pal.border};"
            f"  border-radius: 7px; padding: 6px 8px; font-size: 11px; }}"
            f"QPlainTextEdit {{ font-family: {FONT_MONO}; }}"
            f"QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {pal.accent}; }}"
            f"QListWidget::item {{ padding: 5px 8px; border-radius: 5px; }}"
            f"QListWidget::item:hover {{ background: {pal.bg_hover}; }}"
            f"QListWidget::item:selected {{ background: {pal.accent}; color: #ffffff; }}"
            f"QPushButton {{ background: {pal.bg_secondary}; color: {pal.text_normal}; border: 1px solid {pal.border};"
            f"  border-radius: 7px; padding: 6px 12px; font-size: 11px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {pal.bg_hover}; border-color: {pal.accent}; }}"
            f"QPushButton:checked {{ background: {pal.accent}; color: #ffffff; border-color: {pal.accent}; }}"
            f"QPushButton:disabled {{ color: {pal.text_faint}; }}"
            f"QPushButton#AccentButton {{ background: {pal.accent}; color: #ffffff; border-color: {pal.accent}; }}"
            f"QPushButton#AccentButton:hover {{ background: {pal.accent_hover}; }}"
            f"QPushButton#AccentButton:disabled {{ background: {pal.bg_secondary}; color: {pal.text_faint}; border-color: {pal.border}; }}"
        )
        self._title_lbl.setStyleSheet(
            f"color: {pal.heading_color}; font-size: 15px; font-weight: 700; font-family: {FONT_UI}; background: transparent;"
        )
        for lbl in (self._subtitle_lbl, self._catalog_status_lbl, self._name_lbl, self._meta_lbl):
            lbl.setStyleSheet(
                f"color: {pal.text_muted}; font-size: 11px; font-family: {FONT_UI}; background: transparent;"
            )
        for lbl in (self._gallery_title, self._import_title, self._installed_title, self._preview_title):
            lbl.setStyleSheet(
                f"color: {pal.heading_color}; font-size: 12px; font-weight: 600; font-family: {FONT_UI}; background: transparent;"
            )
        self._open_playground_btn.setIcon(lucide_icon("globe", pal.text_normal, 13))
        self._open_repo_btn.setIcon(lucide_icon("book-open", pal.text_normal, 13))
        self._browse_file_btn.setIcon(lucide_icon("folder-open", pal.text_normal, 13))
        self._preview_btn.setIcon(lucide_icon("sparkles", "#ffffff", 13))
        self._add_use_btn.setIcon(lucide_icon("plus", "#ffffff", 13))
        self._use_installed_btn.setIcon(lucide_icon("check", pal.text_normal, 13))
        self._remove_installed_btn.setIcon(lucide_icon("trash", pal.text_normal, 13))
