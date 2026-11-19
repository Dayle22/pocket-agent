"""Sprite sheet slicer, animation player, and built-in PocketBot generator."""

from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw
from PyQt6.QtGui import QImage, QPixmap

from pocket_agent.engine.fsm import PetAction

COLUMNS = 8
ROWS = 9
CELL_WIDTH = 192
CELL_HEIGHT = 208

ACTION_SPECS: dict[PetAction, tuple[int, list[int]]] = {
    PetAction.IDLE: (0, [280, 120, 120, 140, 140, 320]),
    PetAction.RUNNING_RIGHT: (1, [110, 110, 110, 110, 110, 110, 110, 160]),
    PetAction.RUNNING_LEFT: (2, [110, 110, 110, 110, 110, 110, 110, 160]),
    PetAction.WAVING: (3, [150, 150, 150, 260]),
    PetAction.JUMPING: (4, [130, 130, 130, 130, 260]),
    PetAction.FAILED: (5, [140, 140, 140, 140, 140, 140, 140, 240]),
    PetAction.WAITING: (6, [160, 160, 160, 160, 160, 260]),
    PetAction.RUNNING: (7, [120, 120, 120, 120, 120, 200]),
    PetAction.REVIEW: (8, [140, 140, 140, 140, 140, 240]),
}


def pil_image_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    """Convert a PIL RGBA Image to a PyQt6 QPixmap safely."""
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg)


def generate_pocketbot_sheet(target_path: Path) -> Path:
    """Generate a clean 8x9 (1536x1872) PocketBot spritesheet PNG if missing."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGBA", (COLUMNS * CELL_WIDTH, ROWS * CELL_HEIGHT), (0, 0, 0, 0))

    for action, (row, durations) in ACTION_SPECS.items():
        count = len(durations)
        for col in range(count):
            cell = _draw_pocketbot_frame(action, col, count)
            sheet.paste(cell, (col * CELL_WIDTH, row * CELL_HEIGHT))

    sheet.save(target_path, format="PNG")
    return target_path


def _draw_pocketbot_frame(action: PetAction, col: int, total: int) -> Image.Image:
    """Render one 192x208 frame of the built-in PocketBot companion at 2x supersampling."""
    scale = 2
    w, h = CELL_WIDTH * scale, CELL_HEIGHT * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    phase = (col / max(total, 1)) * math.tau
    bob_y = int(round(math.sin(phase) * 7 * scale))
    tilt_x = 0

    accent = (56, 217, 245, 255)  # Cyan
    glow = (56, 217, 245, 70)
    eye_mode = "normal"

    if action == PetAction.IDLE and col == 2:
        eye_mode = "blink"
    elif action == PetAction.WAITING:
        accent = (251, 191, 36, 255)  # Amber
        glow = (251, 191, 36, 75)
        eye_mode = "think"
    elif action == PetAction.REVIEW:
        accent = (52, 211, 153, 255)  # Mint
        glow = (52, 211, 153, 75)
        eye_mode = "code"
        bob_y = int(round(math.sin(phase * 2) * 3 * scale))
    elif action == PetAction.WAVING:
        accent = (167, 139, 250, 255)  # Violet
        glow = (167, 139, 250, 80)
        eye_mode = "happy"
    elif action == PetAction.JUMPING:
        accent = (52, 211, 153, 255)
        glow = (52, 211, 153, 85)
        bob_y = -int(abs(math.sin(phase)) * 22 * scale)
        eye_mode = "happy"
    elif action == PetAction.FAILED:
        accent = (248, 113, 113, 255)  # Coral red
        glow = (248, 113, 113, 80)
        tilt_x = int(round(math.sin(phase * 2) * 6 * scale))
        eye_mode = "error"
    elif action == PetAction.RUNNING_RIGHT:
        tilt_x = 8 * scale
    elif action == PetAction.RUNNING_LEFT:
        tilt_x = -8 * scale

    cx = w // 2 + tilt_x
    cy = int(h * 0.56) + bob_y

    # Soft ground shadow
    shadow_alpha = max(35, 90 - abs(bob_y) // 2)
    draw.ellipse(
        [w // 2 - 42 * scale, int(h * 0.86), w // 2 + 42 * scale, int(h * 0.92)],
        fill=(10, 14, 24, shadow_alpha),
    )

    # Subtle outer aura behind head
    draw.ellipse(
        [cx - 62 * scale, cy - 60 * scale, cx + 62 * scale, cy + 54 * scale],
        fill=glow,
    )

    # Antenna stalk + glowing orb
    ant_x = cx + (int(math.sin(phase) * 4 * scale) if action == PetAction.WAVING else 0)
    draw.line(
        [(cx, cy - 48 * scale), (ant_x, cy - 72 * scale)],
        fill=(148, 163, 184, 255),
        width=4 * scale,
    )
    draw.ellipse(
        [ant_x - 8 * scale, cy - 82 * scale, ant_x + 8 * scale, cy - 66 * scale],
        fill=accent,
    )

    # Little floating side pods / hands
    left_hand_y = cy + 6 * scale + int(math.sin(phase + 1.0) * 4 * scale)
    right_hand_y = cy + 6 * scale + int(math.cos(phase) * 4 * scale)
    if action == PetAction.WAVING:
        right_hand_y = cy - 22 * scale + int(math.sin(phase * 2) * 10 * scale)
    elif action == PetAction.REVIEW:
        left_hand_y = cy + 18 * scale + (col % 2) * 5 * scale
        right_hand_y = cy + 18 * scale + ((col + 1) % 2) * 5 * scale

    draw.rounded_rectangle(
        [cx - 64 * scale, left_hand_y - 9 * scale, cx - 50 * scale, left_hand_y + 11 * scale],
        radius=7 * scale,
        fill=(51, 65, 85, 255),
        outline=accent,
        width=2 * scale,
    )
    draw.rounded_rectangle(
        [cx + 50 * scale, right_hand_y - 9 * scale, cx + 64 * scale, right_hand_y + 11 * scale],
        radius=7 * scale,
        fill=(51, 65, 85, 255),
        outline=accent,
        width=2 * scale,
    )

    # Main rounded body/head shell
    draw.rounded_rectangle(
        [cx - 50 * scale, cy - 50 * scale, cx + 50 * scale, cy + 44 * scale],
        radius=28 * scale,
        fill=(30, 41, 59, 255),
        outline=(100, 116, 139, 255),
        width=3 * scale,
    )

    # Glossy inner visor screen
    draw.rounded_rectangle(
        [cx - 38 * scale, cy - 34 * scale, cx + 38 * scale, cy + 20 * scale],
        radius=16 * scale,
        fill=(11, 18, 32, 255),
        outline=accent,
        width=2 * scale,
    )

    # Eyes inside visor
    lx, rx, ey = cx - 16 * scale, cx + 16 * scale, cy - 8 * scale
    if eye_mode == "blink":
        draw.line([(lx - 7 * scale, ey), (lx + 7 * scale, ey)], fill=accent, width=3 * scale)
        draw.line([(rx - 7 * scale, ey), (rx + 7 * scale, ey)], fill=accent, width=3 * scale)
    elif eye_mode == "happy":
        draw.arc([lx - 8 * scale, ey - 6 * scale, lx + 8 * scale, ey + 8 * scale], 190, 350, fill=accent, width=3 * scale)
        draw.arc([rx - 8 * scale, ey - 6 * scale, rx + 8 * scale, ey + 8 * scale], 190, 350, fill=accent, width=3 * scale)
    elif eye_mode == "error":
        for ex in (lx, rx):
            draw.line([(ex - 6 * scale, ey - 6 * scale), (ex + 6 * scale, ey + 6 * scale)], fill=accent, width=3 * scale)
            draw.line([(ex - 6 * scale, ey + 6 * scale), (ex + 6 * scale, ey - 6 * scale)], fill=accent, width=3 * scale)
    elif eye_mode == "code":
        # Focused visor eyes + animated code line
        draw.rounded_rectangle([lx - 8 * scale, ey - 4 * scale, lx + 8 * scale, ey + 4 * scale], radius=3 * scale, fill=accent)
        draw.rounded_rectangle([rx - 8 * scale, ey - 4 * scale, rx + 8 * scale, ey + 4 * scale], radius=3 * scale, fill=accent)
        bar_w = ((col % 3) + 1) * 10 * scale
        draw.line([(cx - 18 * scale, cy + 10 * scale), (cx - 18 * scale + bar_w, cy + 10 * scale)], fill=accent, width=2 * scale)
    elif eye_mode == "think":
        # Looking slightly up + orbiting dots above head
        draw.ellipse([lx - 6 * scale, ey - 9 * scale, lx + 6 * scale, ey + 3 * scale], fill=accent)
        draw.ellipse([rx - 6 * scale, ey - 9 * scale, rx + 6 * scale, ey + 3 * scale], fill=accent)
        for i in range(3):
            dx = cx + (i - 1) * 14 * scale
            dy = cy - 92 * scale - (4 * scale if i == col % 3 else 0)
            r = 4 * scale if i == col % 3 else 3 * scale
            draw.ellipse([dx - r, dy - r, dx + r, dy + r], fill=accent)
    else:
        draw.rounded_rectangle([lx - 6 * scale, ey - 9 * scale, lx + 6 * scale, ey + 7 * scale], radius=5 * scale, fill=accent)
        draw.rounded_rectangle([rx - 6 * scale, ey - 9 * scale, rx + 6 * scale, ey + 7 * scale], radius=5 * scale, fill=accent)

    # Cute little chest core indicator
    draw.ellipse([cx - 6 * scale, cy + 27 * scale, cx + 6 * scale, cy + 37 * scale], fill=accent)

    # Sparkles during celebration jump
    if action == PetAction.JUMPING:
        for sx, sy in [(cx - 56 * scale, cy - 58 * scale), (cx + 56 * scale, cy - 52 * scale)]:
            draw.line([(sx - 6 * scale, sy), (sx + 6 * scale, sy)], fill=(250, 204, 21, 255), width=2 * scale)
            draw.line([(sx, sy - 6 * scale), (sx, sy + 6 * scale)], fill=(250, 204, 21, 255), width=2 * scale)

    return img.resize((CELL_WIDTH, CELL_HEIGHT), Image.Resampling.LANCZOS)


class SpriteSheet:
    """Loads and slices a 192x208 grid spritesheet into QPixmaps."""

    def __init__(self, spritesheet_path: Path) -> None:
        self.path = Path(spritesheet_path)
        self.frames: dict[PetAction, list[QPixmap]] = {}
        self.durations: dict[PetAction, list[int]] = {}
        self._load_and_slice()

    def _load_and_slice(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"SpriteSheet not found: {self.path}")

        atlas = Image.open(self.path).convert("RGBA")
        for action, (row, durations) in ACTION_SPECS.items():
            action_frames: list[QPixmap] = []
            for col in range(len(durations)):
                box = (
                    col * CELL_WIDTH,
                    row * CELL_HEIGHT,
                    (col + 1) * CELL_WIDTH,
                    (row + 1) * CELL_HEIGHT,
                )
                cell = atlas.crop(box)
                action_frames.append(pil_image_to_qpixmap(cell))
            self.frames[action] = action_frames
            self.durations[action] = durations

    def get_frame(self, action: PetAction, index: int) -> QPixmap | None:
        flist = self.frames.get(action)
        if not flist:
            return None
        if 0 <= index < len(flist):
            return flist[index]
        return flist[0]

    def get_frame_count(self, action: PetAction) -> int:
        return len(self.frames.get(action, []))

    def get_duration(self, action: PetAction, index: int) -> int:
        dlist = self.durations.get(action, [])
        if dlist and 0 <= index < len(dlist):
            return dlist[index]
        return 150


class SpritePlayer:
    """Advances frame indices according to per-frame durations."""

    def __init__(self, spritesheet: SpriteSheet, static_idle: bool = False) -> None:
        self.spritesheet = spritesheet
        self.current_action: PetAction = PetAction.IDLE
        self.current_frame_index: int = 0
        self.elapsed_ms: int = 0
        self.static_idle: bool = static_idle

    def set_action(self, action: PetAction, reset_index: bool = True) -> None:
        if action != self.current_action:
            self.current_action = action
            if reset_index:
                self.current_frame_index = 0
                self.elapsed_ms = 0

    def update(self, delta_ms: int, is_hovered: bool = False) -> bool:
        if self.current_action == PetAction.IDLE and self.static_idle and not is_hovered:
            if self.current_frame_index != 0:
                self.current_frame_index = 0
                return True
            return False

        self.elapsed_ms += delta_ms
        target_dur = self.spritesheet.get_duration(self.current_action, self.current_frame_index)
        if self.elapsed_ms >= target_dur:
            self.elapsed_ms -= target_dur
            count = self.spritesheet.get_frame_count(self.current_action)
            if count > 0:
                self.current_frame_index = (self.current_frame_index + 1) % count
            return True
        return False

    def current_pixmap(self) -> QPixmap | None:
        return self.spritesheet.get_frame(self.current_action, self.current_frame_index)
