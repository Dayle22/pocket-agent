"""Character and orb selection with a desktop-sized preview."""

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QImageReader, QPixmap, QPixmapCache
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from pocket_agent.engine.catalog import BUILTIN_POCKETBOT_ID, PetInfo, ensure_pocketbot_sheet
from pocket_agent.engine.orbs import OrbPlayer
from pocket_agent.engine.spritesheet import CELL_HEIGHT, CELL_WIDTH


def _portrait(pet: PetInfo, drop_shadow: bool) -> QPixmap:
    key = f"pocket-agent-portrait:{pet.id}:{id(pet)}:{drop_shadow}"
    cached = QPixmapCache.find(key)
    if cached is not None:
        return cached
    if pet.orb_id:
        pix = OrbPlayer(pet.orb_id, custom_orb=pet.custom_orb, drop_shadow=drop_shadow).current_pixmap()
    else:
        path = pet.spritesheet_path
        if pet.id == BUILTIN_POCKETBOT_ID and not drop_shadow:
            path = ensure_pocketbot_sheet(drop_shadow=False)
        reader = QImageReader(str(path))
        reader.setClipRect(QRect(0, 0, CELL_WIDTH, CELL_HEIGHT))
        pix = QPixmap.fromImage(reader.read())
    if pix is None:
        return QPixmap()
    QPixmapCache.insert(key, pix)
    return pix


class CompanionPicker(QWidget):
    selected = pyqtSignal(str)
    size_changed = pyqtSignal(float)
    summary_changed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._drop_shadow = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._choices = QComboBox()
        self._choices.setAccessibleName("Character or orb")
        self._choices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._choices.setMinimumContentsLength(12)
        layout.addWidget(self._choices)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setAccessibleName("Desktop size preview")
        layout.addWidget(self._preview)
        row = QHBoxLayout()
        row.addWidget(QLabel("Desktop size"))
        self._size = QSlider(Qt.Orientation.Horizontal)
        self._size.setAccessibleName("Desktop size")
        self._size.setRange(25, 150)
        self._size.setValue(40)
        self._size.setTracking(False)
        row.addWidget(self._size, 1)
        self._percent = QLabel("40%")
        row.addWidget(self._percent)
        layout.addLayout(row)
        self._choices.activated.connect(self._select)
        self._size.sliderMoved.connect(self._draw)
        self._size.valueChanged.connect(self._resize)

    def set_companions(self, pets: list[PetInfo], current: str, scale: float, *, drop_shadow: bool = True) -> None:
        self._drop_shadow = drop_shadow
        self._choices.clear()
        for pet in pets:
            self._choices.addItem(pet.display_name, pet)
        self._choices.setCurrentIndex(next((i for i, pet in enumerate(pets) if pet.id == current), 0))
        self._size.blockSignals(True)
        self._size.setValue(round(100 * scale))
        self._size.blockSignals(False)
        self._draw(self._size.value())
        self.summary_changed.emit()

    def summary(self) -> str:
        idx = self._choices.currentIndex()
        if idx < 0:
            return "Character, orb and desktop size"
        return f"{self._choices.currentText()} · {self._size.value()}%"

    def set_drop_shadow(self, enabled: bool) -> None:
        self._drop_shadow = enabled
        self._draw(self._size.value())

    def _draw(self, percent: int) -> None:
        self._percent.setText(f"{percent}%")
        pet = self._choices.currentData()
        if pet is not None and self.isVisible():
            pix = _portrait(pet, self._drop_shadow)
            if not pix.isNull():
                self._preview.setPixmap(pix.scaled(round(CELL_WIDTH * percent / 100),
                    round(CELL_HEIGHT * percent / 100), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            else:
                self._preview.setText("Preview unavailable")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._draw(self._size.value())

    def _select(self, index: int) -> None:
        self.selected.emit(self._choices.itemData(index).id)
        self._draw(self._size.value())
        self.summary_changed.emit()

    def _resize(self, percent: int) -> None:
        self._draw(percent)
        self.size_changed.emit(percent / 100)
        self.summary_changed.emit()
