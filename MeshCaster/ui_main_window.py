from __future__ import annotations
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import (
    Qt, QUrl, pyqtSignal, QDateTime, QTimer, QFileSystemWatcher, QSize,
    QRectF, QPointF, QEvent,
)
from PyQt6.QtGui import (
    QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPen,
    QPixmap, QBrush,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateTimeEdit,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSizePolicy, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget, QAbstractSpinBox, QStyledItemDelegate,
)
from meshtastic_worker import MAX_TEXT_BYTES
from storage import JsonStore


DEFAULT_CHANNEL_INDEX = 1
SCHEDULED_MODE_TEXT = "Отправка по расписанию"
SCH_MIN_H = 28
SCH_MAX_H = 54
LABEL_W = 100
CHAT_LOG_LIMIT = 300
LOGO_W = 180
LOGO_H = 180
HEADER_CTRL_HEIGHT = 30


def _make_arrow_png(path: str, direction: str, color: str,
                    w: int = 14, h: int = 9) -> None:
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidth(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    if direction == "up":
        p.drawLine(2, h - 2, w // 2, 2)
        p.drawLine(w // 2, 2, w - 2, h - 2)
    else:
        p.drawLine(2, 2, w // 2, h - 2)
        p.drawLine(w // 2, h - 2, w - 2, 2)
    p.end()
    pm.save(path, "PNG")


def _make_eye_png(path: str, opened: bool, color: str,
                  w: int = 18, h: int = 18) -> None:
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidth(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if opened:
        lens = QPainterPath()
        lens.moveTo(2.0, 9.0)
        lens.quadTo(9.0, 2.0, 16.0, 9.0)
        lens.quadTo(9.0, 16.0, 2.0, 9.0)
        p.drawPath(lens)
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(9.0, 9.0), 2.6, 2.6)
    else:
        arc = QPainterPath()
        arc.moveTo(2.0, 8.0)
        arc.quadTo(9.0, 15.0, 16.0, 8.0)
        p.drawPath(arc)
        p.drawLine(QPointF(4.5, 12.2), QPointF(3.5, 14.2))
        p.drawLine(QPointF(9.0, 13.6), QPointF(9.0, 15.6))
        p.drawLine(QPointF(13.5, 12.2), QPointF(14.5, 14.2))
    p.end()
    pm.save(path, "PNG")


def _make_plug_png(path: str, color: str,
                   w: int = 22, h: int = 22) -> None:
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidth(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(8, 3, 8, 7)
    p.drawLine(14, 3, 14, 7)
    p.drawRoundedRect(5, 7, 12, 9, 2, 2)
    p.drawLine(11, 16, 11, 20)
    p.end()
    pm.save(path, "PNG")


def _make_close_png(path: str, color: str,
                    w: int = 18, h: int = 18) -> None:
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidth(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(5, 5, w - 5, h - 5)
    p.drawLine(w - 5, 5, 5, h - 5)
    p.end()
    pm.save(path, "PNG")


_ARROW_FILES_CACHE: Optional[dict] = None


def _ensure_arrow_files() -> dict:
    global _ARROW_FILES_CACHE
    if _ARROW_FILES_CACHE is not None:
        return _ARROW_FILES_CACHE

    d = tempfile.gettempdir()
    files = {
        "up": os.path.join(d, "mc_arrow_up.png"),
        "down": os.path.join(d, "mc_arrow_down.png"),
        "up_dim": os.path.join(d, "mc_arrow_up_dim.png"),
        "down_dim": os.path.join(d, "mc_arrow_down_dim.png"),
        "eye_open": os.path.join(d, "mc_eye_open.png"),
        "eye_closed": os.path.join(d, "mc_eye_closed.png"),
        "plug_off": os.path.join(d, "mc_plug_off.png"),
        "plug_on": os.path.join(d, "mc_plug_on.png"),
        "plug_conn": os.path.join(d, "mc_plug_conn.png"),
        "close": os.path.join(d, "mc_close.png"),
    }
    _make_arrow_png(files["up"], "up", "#39ff14")
    _make_arrow_png(files["down"], "down", "#39ff14")
    _make_arrow_png(files["up_dim"], "up", "#4a6553")
    _make_arrow_png(files["down_dim"], "down", "#4a6553")
    _make_eye_png(files["eye_open"], True, "#39ff14")
    _make_eye_png(files["eye_closed"], False, "#39ff14")
    _make_plug_png(files["plug_off"], "#ff4d67")
    _make_plug_png(files["plug_on"], "#39ff14")
    _make_plug_png(files["plug_conn"], "#ffc857")
    _make_close_png(files["close"], "#ff8a9a")

    result = {k: v.replace("\\", "/") for k, v in files.items()}
    _ARROW_FILES_CACHE = result
    return result


def _load_logo_pixmap(path: Path, fallback_text: str,
                      w: int = LOGO_W, h: int = LOGO_H) -> QPixmap:
    if path.exists():
        loaded = QPixmap(str(path))
        if not loaded.isNull():
            inner_w = w - 16
            inner_h = h - 16
            return loaded.scaled(
                inner_w, inner_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    pen = QPen(QColor("#2c5a39"))
    pen.setWidth(1)
    pen.setStyle(Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

    p.setPen(QColor("#4a6553"))
    f = p.font()
    f.setPointSize(9)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, fallback_text)
    p.end()
    return pm


def _build_qss() -> str:
    files = _ensure_arrow_files()
    up = files["up"]
    down = files["down"]
    up_dim = files["up_dim"]
    down_dim = files["down_dim"]

    return f"""
QMainWindow, QWidget {{
    background: #0b0f0c;
    color: #c8e6c9;
    font-size: 13px;
}}

QWidget#tabContent {{
    background: #0b0f0c;
    border: 1px solid #1f3a26;
    border-radius: 6px;
}}

QGroupBox {{
    background: transparent;
    border: 1px solid #1f3a26;
    border-radius: 8px;
    margin-top: 10px;
    padding: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #39ff14;
    font-weight: 700;
    letter-spacing: 1px;
}}
QPushButton {{
    background: #121a14;
    border: 1px solid #234a2e;
    border-radius: 6px;
    padding: 7px 12px;
    color: #c8e6c9;
}}
QPushButton:hover {{
    background: #16241a;
    border-color: #39ff14;
    color: #39ff14;
}}
QPushButton:pressed {{ background: #0d1810; }}
QPushButton:disabled {{
    background: #0e1410;
    border-color: #1a2a1e;
    color: #4a6553;
}}
QPushButton#primary {{
    background: #0e3d1f;
    border-color: #39ff14;
    color: #39ff14;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: #145229;
    border-color: #7dff6a;
    color: #c7ffbc;
}}
QPushButton#primary:disabled {{
    background: #12251a;
    border-color: #1f3a26;
    color: #4d6b56;
}}
QPushButton#danger {{
    background: #3a1418;
    border-color: #ff4d67;
    color: #ff8a9a;
}}
QPushButton#danger:hover {{
    background: #4d1a20;
    border-color: #ff6b82;
    color: #ffb0bb;
}}
QPushButton#danger:disabled {{
    background: #1e1013;
    border-color: #3a1a22;
    color: #6d4a52;
}}
QPushButton#eye {{
    background: transparent;
    border: none;
    padding: 2px 2px;
}}
QPushButton#eye:hover {{
    background: #16241a;
    border-radius: 4px;
}}
QPushButton#plugToggle {{
    background: transparent;
    border: 1px solid #234a2e;
    border-radius: 6px;
    padding: 2px;
}}
QPushButton#plugToggle:hover {{
    background: #16241a;
    border-color: #39ff14;
}}
QPushButton#plugToggle:pressed {{
    background: #0d1810;
}}
QPushButton#closeFull {{
    background: transparent;
    border: 1px solid #234a2e;
    border-radius: 6px;
    padding: 2px;
}}
QPushButton#closeFull:hover {{
    background: #3a1418;
    border-color: #ff4d67;
}}
QPushButton#closeFull:pressed {{
    background: #2a0f13;
    border-color: #ff4d67;
}}
QLabel#statusText {{
    background: #070a08;
    border: 1px solid #1f3a26;
    border-radius: 6px;
    padding: 0 12px;
    color: #c8e6c9;
}}
QLabel#statusText[connected="true"] {{
    color: #39ff14;
    border-color: #39ff14;
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    background: #070a08;
    border: 1px solid #1f3a26;
    border-radius: 6px;
    padding: 6px;
    color: #c8e6c9;
    selection-background-color: #39ff14;
    selection-color: #0b0f0c;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus {{
    border-color: #39ff14;
}}
AutoGrowTextEdit {{ padding: 2px 6px; }}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled, QDateTimeEdit:disabled {{
    background: #0a100b;
    color: #4a6553;
    border-color: #16281c;
}}

QSpinBox, QDoubleSpinBox {{
    padding: 6px 26px 6px 6px;
}}
QDateTimeEdit {{
    padding: 6px 26px 6px 6px;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button, QDateTimeEdit::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    height: 14px;
    margin: 3px 3px 0 0;
    border: none;
    background: transparent;
    border-top-right-radius: 4px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QDateTimeEdit::up-button:hover {{
    background: #16241a;
}}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed, QDateTimeEdit::up-button:pressed {{
    background: #0e3d1f;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateTimeEdit::up-arrow {{
    image: url("{up}");
    width: 14px;
    height: 9px;
}}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled, QDateTimeEdit::up-arrow:disabled {{
    image: url("{up_dim}");
}}

QSpinBox::down-button, QDoubleSpinBox::down-button, QDateTimeEdit::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    height: 14px;
    margin: 0 3px 3px 0;
    border: none;
    background: transparent;
    border-bottom-right-radius: 4px;
}}
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover, QDateTimeEdit::down-button:hover {{
    background: #16241a;
}}
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed, QDateTimeEdit::down-button:pressed {{
    background: #0e3d1f;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateTimeEdit::down-arrow {{
    image: url("{down}");
    width: 14px;
    height: 9px;
}}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled, QDateTimeEdit::down-arrow:disabled {{
    image: url("{down_dim}");
}}

QDateTimeEdit::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    margin: 3px 3px 3px 0;
    border: none;
    background: transparent;
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
}}
QDateTimeEdit::drop-down:hover {{
    background: #16241a;
}}
QDateTimeEdit::drop-down:pressed {{
    background: #0e3d1f;
}}
QDateTimeEdit::down-arrow {{
    image: url("{down}");
    width: 14px;
    height: 9px;
}}

QComboBox {{
    padding: 6px 26px 6px 6px;
}}
QComboBox::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    margin: 3px 3px 3px 0;
    border: none;
    background: transparent;
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
}}
QComboBox::drop-down:hover {{
    background: #16241a;
}}
QComboBox::drop-down:pressed {{
    background: #0e3d1f;
}}
QComboBox::down-arrow {{
    image: url("{down}");
    width: 14px;
    height: 9px;
}}
QComboBox::down-arrow:disabled {{
    image: url("{down_dim}");
}}

QTableWidget {{
    background: #070a08;
    alternate-background-color: #0c1410;
    gridline-color: #1a2e20;
    border: 1px solid #1f3a26;
    border-radius: 6px;
    color: #c8e6c9;
}}
QTableWidget::item:selected {{
    background: #145229;
    color: #c7ffbc;
}}
QTableWidget::item {{
    padding: 2px;
}}

QHeaderView {{
    background: transparent;
    border: none;
}}
QHeaderView::section {{
    background: #101a13;
    color: #39ff14;
    padding: 7px;
    border: 0;
    border-right: 1px solid #1a2e20;
    border-bottom: 1px solid #1a2e20;
    font-weight: 600;
}}
QHeaderView::section:first {{
    border-top-left-radius: 5px;
}}
QHeaderView::section:last {{
    border-top-right-radius: 5px;
}}
QHeaderView::section:vertical:first {{
    border-top-left-radius: 5px;
}}
QHeaderView::section:vertical:last {{
    border-bottom-left-radius: 5px;
}}
QHeaderView::section:horizontal:first {{
    border-top-left-radius: 5px;
}}
QHeaderView::section:horizontal:last {{
    border-top-right-radius: 5px;
}}

QTableCornerButton::section {{
    background: #101a13;
    border: none;
    border-top-left-radius: 5px;
}}

QProgressBar {{
    background: #070a08;
    border: 1px solid #1f3a26;
    border-radius: 6px;
    text-align: center;
    color: #c8e6c9;
}}
QProgressBar::chunk {{
    background: #39ff14;
    border-radius: 5px;
}}
QTabBar::tab {{
    background: #0e1611;
    color: #6f8f7a;
    padding: 9px 16px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{
    background: #16241a;
    color: #39ff14;
    border-bottom: 2px solid #39ff14;
    font-weight: 600;
}}
QTabWidget::pane {{
    border: none;
    background: transparent;
    top: -1px;
}}
QComboBox QAbstractItemView {{
    background: #070a08;
    color: #c8e6c9;
    selection-background-color: #145229;
    selection-color: #c7ffbc;
    border: 1px solid #1f3a26;
    outline: none;
}}

QLabel#aboutText a {{
    color: #39ff14;
    text-decoration: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 2px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: transparent;
    border-radius: 1px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: #1f3a26; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0; background: transparent;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 2px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: transparent;
    border-radius: 1px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{ background: #1f3a26; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0; background: transparent;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

QMessageBox {{
    background: #0e1611;
    color: #c8e6c9;
}}
"""


class PasswordWidget(QWidget):
    ICON_HIDDEN = os.path.join(tempfile.gettempdir(), "mc_eye_closed.png")
    ICON_VISIBLE = os.path.join(tempfile.gettempdir(), "mc_eye_open.png")

    def __init__(self, password: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.line = QLineEdit(password)
        self.line.setReadOnly(True)
        self.line.setEchoMode(QLineEdit.EchoMode.Password)
        self.line.setStyleSheet("background:transparent; border:none; padding:0;")

        self.btn = QPushButton()
        self.btn.setObjectName("eye")
        self.btn.setFixedWidth(26)
        self.btn.setFixedHeight(22)
        self.btn.setIconSize(QSize(18, 18))
        self.btn.setCheckable(True)
        self.btn.setToolTip("Показать / скрыть пароль")
        self.btn.setIcon(QIcon(self.ICON_HIDDEN))
        self.btn.clicked.connect(self._toggle)

        layout.addWidget(self.line, 1)
        layout.addWidget(self.btn)

    def _toggle(self) -> None:
        if self.btn.isChecked():
            self.line.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn.setIcon(QIcon(self.ICON_VISIBLE))
        else:
            self.line.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn.setIcon(QIcon(self.ICON_HIDDEN))


class CompactCellDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setFrame(False)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.setStyleSheet(
            "QLineEdit {"
            "  background: #070a08;"
            "  color: #c8e6c9;"
            "  border: 1px solid #39ff14;"
            "  border-radius: 0;"
            "  padding: 0 2px;"
            "  margin: 0;"
            "  selection-background-color: #39ff14;"
            "  selection-color: #0b0f0c;"
            "}"
        )
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class CenteredCheckBox(QWidget):
    stateChanged = pyqtSignal(int)

    BOX_SIZE = 16
    RADIUS = 5

    def __init__(self, checked: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet("background: transparent;")

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, state: bool) -> None:
        state = bool(state)
        if state == self._checked:
            return
        self._checked = state
        self.update()
        self.stateChanged.emit(2 if state else 0)

    def toggle(self) -> None:
        self.setChecked(not self._checked)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        size = float(self.BOX_SIZE)
        box = QRectF(cx - size / 2.0, cy - size / 2.0, size, size)

        if self._checked:
            border_color = QColor("#7dff6a" if self._hover else "#39ff14")
            fill_color = border_color
            border_w = 1.0
            check_color = QColor("#0b0f0c")
        else:
            border_color = QColor("#39ff14" if self._hover else "#2c5a39")
            fill_color = QColor("#070a08")
            border_w = 2.0
            check_color = None

        fill_path = QPainterPath()
        fill_path.addRoundedRect(box, self.RADIUS, self.RADIUS)
        p.fillPath(fill_path, QBrush(fill_color))

        pen = QPen(border_color)
        pen.setWidthF(border_w)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        border_rect = box.adjusted(border_w / 2.0, border_w / 2.0,
                                   -border_w / 2.0, -border_w / 2.0)
        border_path = QPainterPath()
        border_path.addRoundedRect(border_rect, self.RADIUS, self.RADIUS)
        p.drawPath(border_path)

        if self._checked and check_color is not None:
            cpen = QPen(check_color)
            cpen.setWidthF(2.0)
            cpen.setCapStyle(Qt.PenCapStyle.RoundCap)
            cpen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(cpen)
            s = box.width()
            x0 = box.left()
            y0 = box.top()
            p.drawLine(
                QPointF(x0 + s * 0.25, y0 + s * 0.52),
                QPointF(x0 + s * 0.44, y0 + s * 0.72),
            )
            p.drawLine(
                QPointF(x0 + s * 0.44, y0 + s * 0.72),
                QPointF(x0 + s * 0.78, y0 + s * 0.30),
            )

        p.end()


class LabeledCheckBox(QWidget):
    """Чекбокс с подписью. Кликается и по квадрату, и по тексту."""

    stateChanged = pyqtSignal(int)

    def __init__(
        self,
        text: str,
        checked: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._cb = CenteredCheckBox(checked)
        self._cb.setFixedSize(18, 18)
        self._cb.stateChanged.connect(self.stateChanged)

        self._label = QLabel(text)
        self._label.setCursor(Qt.CursorShape.PointingHandCursor)

        layout.addWidget(self._cb)
        layout.addWidget(self._label)
        layout.addStretch()

        # QLabel не имеет своего clicked — подменяем обработчик.
        self._label.mousePressEvent = self._on_label_press  # type: ignore[method-assign]

    def _on_label_press(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._cb.toggle()
            event.accept()

    def isChecked(self) -> bool:
        return self._cb.isChecked()

    def setChecked(self, state: bool) -> None:
        self._cb.setChecked(state)


class ClickableLogo(QLabel):
    def __init__(
        self,
        pixmap: QPixmap,
        url: str,
        tooltip: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._url = url or ""
        self.setObjectName("logoBox")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPixmap(pixmap)
        self.setFixedSize(LOGO_W, LOGO_H)
        if self._url:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QLabel#logoBox {"
            "  background: #070a08;"
            "  border: 1px solid #1f3a26;"
            "  border-radius: 6px;"
            "}"
            "QLabel#logoBox:hover {"
            "  border-color: #39ff14;"
            "  background: #0e1a13;"
            "}"
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._url:
            QDesktopServices.openUrl(QUrl(self._url))
            event.accept()
            return
        super().mousePressEvent(event)


class TextProgressBar(QProgressBar):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setTextVisible(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        v = self.value()
        mn = self.minimum()
        mx = self.maximum()
        ratio = 0.0 if mx <= mn else (v - mn) / float(mx - mn)
        chunk_x = int(self.width() * ratio)

        font = p.font()
        font.setBold(True)
        p.setFont(font)

        text = self.text() or str(v)
        rect = self.rect()

        if chunk_x > 0:
            p.save()
            p.setClipRect(0, 0, chunk_x, self.height())
            p.setPen(QColor("#0b0f0c"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            p.restore()

        if chunk_x < self.width():
            p.save()
            p.setClipRect(chunk_x, 0, self.width() - chunk_x, self.height())
            p.setPen(QColor("#c8e6c9"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            p.restore()

        p.end()


class AutoGrowTextEdit(QTextEdit):
    def __init__(
        self,
        min_height: int = SCH_MIN_H,
        max_height: int = SCH_MAX_H,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._min_h = min_height
        self._max_h = max_height
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(min_height)
        self.textChanged.connect(self._recompute)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._recompute()

    def _recompute(self) -> None:
        doc = self.document()
        w = self.viewport().width()
        if w <= 0:
            return
        doc.setTextWidth(w)
        target = int(doc.size().height()) + 12
        target = max(self._min_h, min(self._max_h, target))
        if target != self.height():
            self.setFixedHeight(target)


class MainWindow(QMainWindow):
    connect_requested = pyqtSignal(str)
    disconnect_requested = pyqtSignal()
    ports_requested = pyqtSignal()
    start_requested = pyqtSignal(str, str, int)
    pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    resume_requested = pyqtSignal(str)
    resume_current_requested = pyqtSignal()
    schedule_add = pyqtSignal(str, str, str, bool)
    schedule_delete = pyqtSignal(str)
    schedule_edit = pyqtSignal(str, str, str, str, bool)
    schedule_run_now = pyqtSignal(str)
    any_ack_changed = pyqtSignal(bool)

    def __init__(self, badges_path: Path) -> None:
        super().__init__()
        self.badges_path = badges_path
        self.notes_path = badges_path.parent / "notes.json"
        self.notes_store = JsonStore(self.notes_path)

        # UI-настройки (пока только any_ack).
        self._settings_path = badges_path.parent / "ui_settings.json"
        self._settings_store = JsonStore(self._settings_path)
        _settings = self._settings_store.load({})
        if isinstance(_settings, dict):
            self._any_ack = bool(_settings.get("any_ack", False))
        else:
            self._any_ack = False

        self.chat_log_dir = badges_path.parent / "chat_logs"
        self._chat_logs: dict[int, list[dict]] = {}
        self._active_chat_channel: Optional[int] = None
        self._saved_dm_channel: int = DEFAULT_CHANNEL_INDEX
        self._channel_names: dict[int, str] = {}

        self._chat_dirty: set[int] = set()
        self._chat_save_timer = QTimer(self)
        self._chat_save_timer.setSingleShot(True)
        self._chat_save_timer.setInterval(1000)
        self._chat_save_timer.timeout.connect(self._flush_chat_logs)

        self.logos_dir = badges_path.parent / "logos"

        self.notes: dict[str, str] = {}
        self.badges: list[tuple[str, str, str]] = []
        self.selected: set[str] = set()
        self._loading_badges = False
        self._loading_schedule = False
        self._sch_trimming = False
        self._msg_trimming = False
        self._sending = False
        self._paused = False
        self._connected = False
        self._saved_mode_index = 0
        self._schedule_cache: dict[str, dict] = {}

        self._badges_reload_timer = QTimer(self)
        self._badges_reload_timer.setSingleShot(True)
        self._badges_reload_timer.timeout.connect(self._auto_reload_badges)

        self._badges_watcher = QFileSystemWatcher(self)
        self._install_badges_watcher()

        self.setWindowTitle("Meshtastic Broadcast Manager")

        icon_candidates = [
            self.badges_path.parent / "icon.ico",
            self.badges_path.parent / "icon.png",
            Path(getattr(sys, "_MEIPASS", "")) / "icon.ico"
            if getattr(sys, "_MEIPASS", None) else None,
        ]
        for cand in icon_candidates:
            if cand and cand.exists():
                icon = QIcon(str(cand))
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    QApplication.setWindowIcon(icon)
                    break

        self.resize(1036, 750)
        self.setStyleSheet(_build_qss())
        self._load_notes()
        self._migrate_legacy_chat()
        self._build()
        self.reload_badges()

        # До подключения активного канала нет, чат пуст.
        self._active_chat_channel = None
        self._update_chat_title()
        self._render_active_chat()

        self._apply_sending_state()

    # ---------------------------------------------------- migration

    def _migrate_legacy_chat(self) -> None:
        legacy = self.badges_path.parent / "zn_chat_log.json"
        target = self.chat_log_dir / f"chat_ch{DEFAULT_CHANNEL_INDEX}.json"
        if not legacy.exists() or target.exists():
            return
        try:
            self.chat_log_dir.mkdir(parents=True, exist_ok=True)
            legacy.rename(target)
        except Exception:
            pass

    # ---------------------------------------------------- file watching

    def _install_badges_watcher(self) -> None:
        try:
            parent = self.badges_path.parent
            if parent.exists():
                self._badges_watcher.addPath(str(parent))
            if self.badges_path.exists():
                self._badges_watcher.addPath(str(self.badges_path))
        except Exception:
            pass
        self._badges_watcher.fileChanged.connect(self._on_badges_fs_event)
        self._badges_watcher.directoryChanged.connect(self._on_badges_fs_event)

    def _on_badges_fs_event(self, _path: str) -> None:
        try:
            if (self.badges_path.exists()
                    and str(self.badges_path) not in self._badges_watcher.files()):
                self._badges_watcher.addPath(str(self.badges_path))
        except Exception:
            pass
        self._badges_reload_timer.start(250)

    def _auto_reload_badges(self) -> None:
        before = list(self.badges)
        self.reload_badges()
        if list(self.badges) != before:
            self.log("badges_list.txt изменён — список перезагружен", "info")

    # ------------------------------------------------------------------ notes

    def _load_notes(self) -> None:
        data = self.notes_store.load({})
        if isinstance(data, dict):
            self.notes = {
                str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))
            }
        else:
            self.notes = {}

    def _save_notes(self) -> None:
        self.notes_store.save(self.notes)

    def _on_badge_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_badges:
            return
        if item.column() != 3:
            return
        node_item = self.badge_table.item(item.row(), 1)
        if node_item is None:
            return
        node_id = node_item.text()
        if not node_id:
            return
        text = item.text()
        if text:
            self.notes[node_id] = text
        else:
            self.notes.pop(node_id, None)
        self._save_notes()

    # ------------------------------------------------------------ chat

    def _chat_file(self, channel: int) -> Path:
        return self.chat_log_dir / f"chat_ch{channel}.json"

    def _load_chat_log(self, channel: int) -> list[dict]:
        if channel in self._chat_logs:
            return self._chat_logs[channel]
        store = JsonStore(self._chat_file(channel))
        data = store.load([])
        if isinstance(data, list):
            entries = [x for x in data if isinstance(x, dict)][-CHAT_LOG_LIMIT:]
        else:
            entries = []
        self._chat_logs[channel] = entries
        return entries

    def _save_chat_log(self, channel: int) -> None:
        entries = self._chat_logs.get(channel, [])
        JsonStore(self._chat_file(channel)).save(entries)

    def _render_active_chat(self) -> None:
        self.chat_view.clear()
        if self._active_chat_channel is None:
            return
        entries = self._load_chat_log(self._active_chat_channel)
        for entry in entries:
            ts = str(entry.get("time", ""))
            node = str(entry.get("node", ""))
            text = str(entry.get("text", ""))
            self.chat_view.appendPlainText(f"[{ts}] {node}: {text}")

    def append_chat_message(self, channel: int, ts: str, node: str, text: str) -> None:
        entries = self._load_chat_log(channel)
        entries.append({"time": ts, "node": node, "text": text})
        if len(entries) > CHAT_LOG_LIMIT:
            del entries[:-CHAT_LOG_LIMIT]
        self._chat_logs[channel] = entries
        # Отложенная запись: fsync на каждое сообщение подвешивал GUI.
        self._chat_dirty.add(channel)
        if not self._chat_save_timer.isActive():
            self._chat_save_timer.start()
        if channel == self._active_chat_channel:
            self.chat_view.appendPlainText(f"[{ts}] {node}: {text}")

    def _update_chat_title(self) -> None:
        if self._active_chat_channel is None:
            self.chat_group.setTitle("Чат")
            return
        name = self._channel_names.get(self._active_chat_channel, "")
        if name:
            self.chat_group.setTitle(f"Чат: {name}")
        else:
            self.chat_group.setTitle(f"Чат: {self._active_chat_channel}")

    def _flush_chat_logs(self) -> None:
        for channel in sorted(self._chat_dirty):
            self._save_chat_log(channel)
        self._chat_dirty.clear()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._flush_chat_logs()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_fullscreen_ui()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # showFullScreen() из main() может выставиться до старта цикла событий —
        # обновим видимость кнопки на следующем тике.
        QTimer.singleShot(0, self._update_fullscreen_ui)

    def _update_fullscreen_ui(self) -> None:
        self.close_btn.setVisible(self.isFullScreen())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # F11 — переключить полноэкранный режим.
        if event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
            event.accept()
            return
        # Esc — выйти из fullscreen (обычное закрытие окна Esc не делает).
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            event.accept()
            return
        super().keyPressEvent(event)

    def current_dm_channel(self) -> int:
        """Канал для DM-рассылок (в т.ч. по расписанию): текущий выбор
        в комбобоксе, иначе последний выбранный, иначе дефолт."""
        data = self.channel.currentData()
        try:
            if data is not None:
                return int(data)
        except (TypeError, ValueError):
            pass
        if self._saved_dm_channel is not None:
            return int(self._saved_dm_channel)
        return DEFAULT_CHANNEL_INDEX

    def any_ack(self) -> bool:
        """Текущее состояние режима «принимать ACK от любой ноды»."""
        return bool(self._any_ack)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _style_table(table: QTableWidget) -> None:
        table.setFrameShape(QTableWidget.Shape.NoFrame)
        table.viewport().setAutoFillBackground(False)
        table.viewport().setStyleSheet("background: transparent;")
        corner = table.findChild(QWidget, "qt_scrollarea_corner")
        if corner is not None:
            corner.setStyleSheet("background: transparent;")

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("ZERONIGHTS 2026 MESHCASTER")
        title.setStyleSheet(
            "font-family:'Consolas','Courier New',monospace;"
            "font-size:20px;font-weight:800;color:#39ff14;letter-spacing:2px;"
        )

        icons = _ensure_arrow_files()

        self.status_text = QLabel("Нода не подключена")
        self.status_text.setObjectName("statusText")
        self.status_text.setFixedHeight(HEADER_CTRL_HEIGHT)
        self.status_text.setMinimumWidth(220)
        self.status_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_text.setProperty("connected", False)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(80)
        self.port_combo.setFixedHeight(HEADER_CTRL_HEIGHT)

        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.setToolTip("Обновить COM-порты")
        self.refresh_btn.setFixedHeight(HEADER_CTRL_HEIGHT)
        self.refresh_btn.clicked.connect(self.ports_requested.emit)

        connect_btn = QPushButton("Подключить")
        connect_btn.setObjectName("primary")
        connect_btn.setFixedHeight(HEADER_CTRL_HEIGHT)
        connect_btn.clicked.connect(self.toggle_connection)
        self.connect_button = connect_btn

        self.plug_btn = QPushButton()
        self.plug_btn.setObjectName("plugToggle")
        self.plug_btn.setFixedSize(HEADER_CTRL_HEIGHT, HEADER_CTRL_HEIGHT)
        self.plug_btn.setIconSize(QSize(22, 22))
        self.plug_btn.setIcon(QIcon(icons["plug_off"]))
        self.plug_btn.setToolTip("Показать / скрыть настройки подключения")
        self.plug_btn.clicked.connect(self._toggle_conn_controls)

        self.close_btn = QPushButton()
        self.close_btn.setObjectName("closeFull")
        self.close_btn.setFixedSize(HEADER_CTRL_HEIGHT, HEADER_CTRL_HEIGHT)
        self.close_btn.setIconSize(QSize(22, 22))
        self.close_btn.setIcon(QIcon(icons["close"]))
        self.close_btn.setToolTip("Закрыть приложение")
        self.close_btn.clicked.connect(self.close)
        self.close_btn.setVisible(False)

        header.addSpacing(10)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.status_text)
        header.addWidget(self.port_combo)
        header.addWidget(self.refresh_btn)
        header.addWidget(self.connect_button)
        header.addWidget(self.plug_btn)
        header.addWidget(self.close_btn)

        self._conn_controls = (
            self.status_text,
            self.port_combo,
            self.refresh_btn,
            self.connect_button,
        )
        for w in self._conn_controls:
            w.setVisible(False)

        outer.addLayout(header)

        self.tabs = QTabWidget()

        self.broadcast_tab = QWidget()
        self.broadcast_tab.setObjectName("tabContent")
        self.broadcast_tab.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )

        self.schedule_tab = QWidget()
        self.schedule_tab.setObjectName("tabContent")
        self.schedule_tab.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )

        self.about_tab = QWidget()
        self.about_tab.setObjectName("tabContent")
        self.about_tab.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )

        self._build_broadcast_tab()
        self._build_schedule_tab()
        self._build_about_tab()

        self.tabs.addTab(self.broadcast_tab, "Рассылка")
        self.tabs.addTab(self.schedule_tab, "Расписание")
        self.tabs.addTab(self.about_tab, "О программе")

        splitter = QSplitter(Qt.Orientation.Vertical)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        log_group = QGroupBox("Системный журнал")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        log_layout.addWidget(self.log_view)
        bottom_layout.addWidget(log_group, 1)

        self.chat_group = QGroupBox("Чат")
        chat_layout = QVBoxLayout(self.chat_group)
        self.chat_view = QPlainTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setMaximumBlockCount(CHAT_LOG_LIMIT)
        chat_layout.addWidget(self.chat_view)
        bottom_layout.addWidget(self.chat_group, 1)

        splitter.addWidget(self.tabs)
        splitter.addWidget(bottom)
        splitter.setSizes([495, 285])
        outer.addWidget(splitter, 1)

    @staticmethod
    def _row_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(LABEL_W)
        lbl.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        return lbl

    def _rebuild_modes(self) -> None:
        if self._sending:
            return
        prev_data = self.mode.currentData()

        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItem("Отправить только выбранным", "dm")
        for idx in sorted(self._channel_names.keys()):
            nm = self._channel_names[idx]
            self.mode.addItem(f"В канал ({idx}) {nm}", f"ch:{idx}")

        restored = False
        if prev_data is not None:
            for i in range(self.mode.count()):
                if self.mode.itemData(i) == prev_data:
                    self.mode.setCurrentIndex(i)
                    restored = True
                    break
        if not restored:
            self.mode.setCurrentIndex(0)
        self.mode.blockSignals(False)

    def _select_channel_in_combo(self, ch_idx: int) -> None:
        for i in range(self.channel.count()):
            if self.channel.itemData(i) == ch_idx:
                if self.channel.currentIndex() != i:
                    self.channel.setCurrentIndex(i)
                return

    def _build_broadcast_tab(self) -> None:
        layout = QHBoxLayout(self.broadcast_tab)

        left = QGroupBox("Бейджики")
        ll = QVBoxLayout(left)
        row = QHBoxLayout()
        self.badge_count = QLabel("0 бейджиков")
        btn_all = QPushButton("Выбрать все")
        btn_clear = QPushButton("Снять выделение")
        btn_all.clicked.connect(self.select_all)
        btn_clear.clicked.connect(self.clear_selection)
        row.addWidget(self.badge_count)
        row.addStretch()
        row.addWidget(btn_all)
        row.addWidget(btn_clear)
        ll.addLayout(row)

        self.badge_table = QTableWidget(0, 5)
        self.badge_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.badge_table.setHorizontalHeaderLabels(
            ["Имя", "NodeID", "Пароль", "Заметка", "X"]
        )
        self.badge_table.setAlternatingRowColors(True)
        self.badge_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.badge_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        hdr = self.badge_table.horizontalHeader()
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.badge_table.setColumnWidth(0, 45)
        self.badge_table.setColumnWidth(1, 90)
        self.badge_table.setColumnWidth(2, 90)
        self.badge_table.setColumnWidth(4, 25)
        self._note_delegate = CompactCellDelegate(self.badge_table)
        self.badge_table.setItemDelegateForColumn(3, self._note_delegate)
        self.badge_table.itemChanged.connect(self._on_badge_item_changed)
        self._style_table(self.badge_table)
        ll.addWidget(self.badge_table)
        layout.addWidget(left, 2)

        right = QGroupBox("Сообщение")
        form = QFormLayout(right)
        form.setSpacing(4)
        form.setContentsMargins(12, 8, 12, 8)

        self.message = QTextEdit()
        self.message.setPlaceholderText("Введите сообщение для отправки...")
        self.message.setMinimumHeight(112)
        self.message.setMaximumHeight(600)
        self.message.textChanged.connect(self.update_counter)
        self.counter = QLabel("0 / 233 байт")
        self.counter.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.mode = QComboBox()
        self.mode.addItem("Отправить только выбранным", "dm")
        self.mode.currentIndexChanged.connect(self._on_mode_changed)

        self.channel = QComboBox()
        self.channel.setMinimumWidth(200)
        self.channel.setFixedHeight(30)
        self.channel.addItem("-", None)
        self.channel.currentIndexChanged.connect(self._on_channel_changed)

        self.any_ack_cb = LabeledCheckBox(
            "Принимать ACK от любой ноды",
            self._any_ack,
        )
        self.any_ack_cb.setToolTip(
            "Если включено — не ждём ответа именно от получателя, "
            "а принимаем любой ROUTING-ответ с тем же requestId. "
            "Сильно ускоряет рассылку на большие списки, когда "
            "часть бейджиков оффлайн, но ACK уже не гарантирует, "
            "что принял именно адресат."
        )
        self.any_ack_cb.stateChanged.connect(self._on_any_ack_toggled)

        self.progress = TextProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress_label = QLabel("Готово к отправке")
        self.progress_label.setStyleSheet(
            "QLabel { padding-top: 4px; padding-bottom: 6px; }"
        )
        form.addRow("Текст:", self.message)
        form.addRow("", self.counter)
        form.addRow("Режим:", self.mode)
        form.addRow("Канал:", self.channel)
        form.addRow("ACK:", self.any_ack_cb)
        form.addRow("Прогресс:", self.progress)
        form.addRow("", self.progress_label)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Старт")
        self.start_btn.setObjectName("primary")
        self.pause_btn = QPushButton("Пауза")
        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setObjectName("danger")
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.pause_btn.clicked.connect(self.pause_requested.emit)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.pause_btn)
        buttons.addWidget(self.stop_btn)
        form.addRow("", buttons)
        layout.addWidget(right, 1)

    def _build_schedule_tab(self) -> None:
        layout = QVBoxLayout(self.schedule_tab)

        form_box = QGroupBox("Новая задача")
        outer = QVBoxLayout(form_box)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(6)

        self.sch_message = AutoGrowTextEdit()
        self.sch_message.setStyleSheet(
            "AutoGrowTextEdit {"
            "  background: #070a08;"
            "  border: 1px solid #1f3a26;"
            "  border-radius: 6px;"
            "  padding: 2px 6px;"
            "  color: #c8e6c9;"
            "  selection-background-color: #39ff14;"
            "  selection-color: #0b0f0c;"
            "}"
        )
        self.sch_message.setPlaceholderText("Текст сообщения")
        self.sch_message.textChanged.connect(self._on_sch_message_changed)

        self.sch_counter = QLabel("0 / 233 байт")
        self.sch_counter.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.sch_counter.setMinimumWidth(84)

        msg_row = QHBoxLayout()
        msg_row.setSpacing(8)
        msg_row.setContentsMargins(0, 0, 0, 0)
        msg_row.addWidget(self._row_label("Сообщение:"))
        msg_row.addWidget(self.sch_message, 1)
        msg_row.addWidget(self.sch_counter)
        outer.addLayout(msg_row)

        self.sch_recipients = QLineEdit("*")
        self.sch_recipients.setToolTip("* или список через запятую: 1596227832,123456789")
        self.sch_recipients.setFixedHeight(30)

        rec_row = QHBoxLayout()
        rec_row.setSpacing(8)
        rec_row.setContentsMargins(0, 0, 0, 0)
        rec_row.addWidget(self._row_label("Получатели:"))
        rec_row.addWidget(self.sch_recipients, 1)
        outer.addLayout(rec_row)

        self.sch_date = QDateTimeEdit()
        self.sch_date.setDateTime(QDateTime.currentDateTime())
        self.sch_date.setDisplayFormat("yyyy-MM-dd")
        self.sch_date.setCalendarPopup(True)
        self.sch_date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.sch_date.setFixedHeight(30)
        self.sch_date.setFixedWidth(140)

        self.sch_time = QDateTimeEdit()
        self.sch_time.setDateTime(QDateTime.currentDateTime())
        self.sch_time.setDisplayFormat("HH:mm:ss")
        self.sch_time.setCalendarPopup(False)
        self.sch_time.setFixedHeight(30)
        self.sch_time.setFixedWidth(120)

        add_btn = QPushButton("Добавить задачу")
        add_btn.setObjectName("primary")
        add_btn.setFixedHeight(30)
        add_btn.setMinimumWidth(130)
        add_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        add_btn.clicked.connect(self._on_add_schedule_clicked)

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        run_row.setContentsMargins(0, 0, 0, 0)
        run_row.addWidget(self._row_label("Запуск:"))
        run_row.addWidget(self.sch_date)
        run_row.addWidget(self.sch_time)
        run_row.addWidget(add_btn, 1)
        outer.addLayout(run_row)

        layout.addWidget(form_box, 0)

        self.schedule_table = QTableWidget(0, 5)
        self.schedule_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.schedule_table.setHorizontalHeaderLabels(
            ["ID", "Сообщение", "Получатели", "Запуск", "Вкл"]
        )
        hdr = self.schedule_table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.schedule_table.setColumnWidth(0, 80)
        self.schedule_table.setColumnWidth(2, 140)
        self.schedule_table.setColumnWidth(3, 150)
        self.schedule_table.setColumnWidth(4, 40)
        self.schedule_table.itemChanged.connect(self._on_schedule_item_changed)
        self._style_table(self.schedule_table)
        self._sched_delegate = CompactCellDelegate(self.schedule_table)
        for col in (1, 2, 3):
            self.schedule_table.setItemDelegateForColumn(col, self._sched_delegate)
        layout.addWidget(self.schedule_table, 1)

        buttons = QHBoxLayout()
        run_btn = QPushButton("Выполнить немедленно")
        run_btn.setObjectName("primary")
        run_btn.clicked.connect(self.run_schedule_now)
        del_btn = QPushButton("Удалить выбранную задачу")
        del_btn.clicked.connect(self.delete_schedule)
        buttons.addWidget(run_btn)
        buttons.addWidget(del_btn)
        layout.addLayout(buttons)

    def _build_about_tab(self) -> None:
        layout = QVBoxLayout(self.about_tab)
        layout.setContentsMargins(40, 20, 40, 20)
        layout.setSpacing(4)

        about_html = (
            "<div style='font-size:14px; line-height:1.7; color:#c8e6c9;'>"
            "<p style='margin:6px 0;'>"
            "Софт написан специально для конференции "
            "<a href='https://zeronights.ru/' "
            "style='color:#39ff14; text-decoration:none;'>ZeroNights 2026</a> "
            "при генеральной поддержке "
            "<a href='https://dsec.ru/' "
            "style='color:#39ff14; text-decoration:none;'>Digital Security</a>."
            "</p>"
            "<p style='margin:6px 0;'>"
            "Участником сообщества "
            "<a href='https://t.me/dc78422' "
            "style='color:#39ff14; text-decoration:none;'>DC78422</a> "
            "- "
            "<a href='https://t.me/KainSamara' "
            "style='color:#39ff14; text-decoration:none;'>"
            "Сергеем Нордом</a>"
            " aka "
            "<a href='https://t.me/RottenMechanism' "
            "style='color:#39ff14; text-decoration:none;'>"
            "Rotten Mechanism</a>."
            "</p>"
            "<p style='margin:18px 0 6px 0;'>"
            "Сделано на базе проекта "
            "<a href='https://meshtastic.org/' "
            "style='color:#39ff14; text-decoration:none;'>Meshtastic</a>."
            "</p>"
            "</div>"
        )

        about_label = QLabel(about_html)
        about_label.setObjectName("aboutText")
        about_label.setTextFormat(Qt.TextFormat.RichText)
        about_label.setOpenExternalLinks(True)
        about_label.setWordWrap(True)
        about_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        about_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        about_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(about_label, 1)

        bottom_block = QVBoxLayout()
        bottom_block.setContentsMargins(0, 0, 0, 0)
        bottom_block.setSpacing(7)

        hint = QLabel("Причастные организации, сообщества и проекты:")
        hint.setStyleSheet("color:#6f8f7a; font-size:12px;")
        hint.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        bottom_block.addWidget(hint)

        logos_row = QHBoxLayout()
        logos_row.setSpacing(8)
        logos_row.setContentsMargins(0, 0, 0, 0)

        logos = [
            ("zeronights.png", "ZeroNights", "https://zeronights.ru/"),
            ("digital_security.png", "Digital Security", "https://dsec.ru/"),
            ("dc78422.png", "DC78422", "https://dc78422.ru/"),
            ("rotten_mechanism.png", "Rotten Mechanism",
             "https://t.me/RottenMechanism"),
            ("meshtastic.png", "Meshtastic", "https://meshtastic.org/"),
        ]

        logos_row.addStretch(1)
        for filename, tooltip, url in logos:
            pm = _load_logo_pixmap(self.logos_dir / filename, tooltip)
            logo = ClickableLogo(pm, url, tooltip)
            logos_row.addWidget(logo, 0)
            logos_row.addStretch(1)

        bottom_block.addLayout(logos_row)

        layout.addLayout(bottom_block)

    # -------------------------------------------------------- channels

    def set_channels(self, channels: list) -> None:
        entries: list[tuple[int, str]] = []
        for entry in channels or []:
            try:
                idx = int(entry[0])
                name = str(entry[1])
            except Exception:
                continue
            entries.append((idx, name))

        # Пустой список = отключились / ошибка подключения.
        # Сбрасываем список каналов, режимы, очищаем чат и ставим "-".
        if not entries:
            self._channel_names = {}
            self._flush_chat_logs()
            self._chat_logs = {}
            self._active_chat_channel = None
            # _saved_dm_channel НЕ трогаем: запомним выбор до отключения.

            self.channel.blockSignals(True)
            self.channel.clear()
            self.channel.addItem("-", None)
            self.channel.setCurrentIndex(0)
            self.channel.blockSignals(False)

            self.chat_view.clear()

            self._rebuild_modes()
            self._update_chat_title()
            self._apply_sending_state()
            return

        self._channel_names = {i: n for i, n in entries}

        prev_data = self.channel.currentData()
        try:
            prev_idx = int(prev_data) if prev_data is not None else None
        except Exception:
            prev_idx = None

        # Приоритет: текущий выбор → сохранённый DM-канал → дефолт.
        target = prev_idx
        if target is None:
            target = self._saved_dm_channel
        if target is None:
            target = DEFAULT_CHANNEL_INDEX

        self.channel.blockSignals(True)
        self.channel.clear()
        for idx, name in entries:
            self.channel.addItem(f"{idx} - {name}", idx)

        chosen = 0
        for i in range(self.channel.count()):
            if self.channel.itemData(i) == target:
                chosen = i
                break
        else:
            for i in range(self.channel.count()):
                if self.channel.itemData(i) == DEFAULT_CHANNEL_INDEX:
                    chosen = i
                    break
        self.channel.setCurrentIndex(chosen)
        self.channel.blockSignals(False)

        data = self.channel.currentData()
        try:
            self._active_chat_channel = int(data) if data is not None else None
        except Exception:
            self._active_chat_channel = None

        self._rebuild_modes()
        self._update_chat_title()
        self._render_active_chat()
        self._on_mode_changed(self.mode.currentIndex())

    def _on_channel_changed(self, _index: int) -> None:
        data = self.channel.currentData()
        try:
            idx = int(data) if data is not None else None
        except Exception:
            idx = None
        self._active_chat_channel = idx

        # Запоминаем выбор только в режиме DM и только если есть реальный
        # канал (в отключённом состоянии data=None — не трогаем память).
        if self.mode.currentData() == "dm" and idx is not None:
            self._saved_dm_channel = idx

        self._update_chat_title()
        self._render_active_chat()

    # -------------------------------------------------------- sending state

    def set_sending(self, scheduled: bool = False) -> None:
        if self._sending:
            return
        self._sending = True
        self._paused = False
        self._saved_mode_index = self.mode.currentIndex()

        if scheduled:
            self.mode.blockSignals(True)
            self.mode.addItem(SCHEDULED_MODE_TEXT)
            self.mode.setCurrentIndex(self.mode.count() - 1)
            self.mode.blockSignals(False)
        self._apply_sending_state()

    def set_paused(self) -> None:
        if not self._sending:
            return
        self._paused = True
        self._apply_sending_state()

    def clear_sending(self) -> None:
        if not self._sending:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress_label.setText("Готово к отправке")
            return
        self._sending = False
        self._paused = False
        for i in range(self.mode.count()):
            if self.mode.itemText(i) == SCHEDULED_MODE_TEXT:
                self.mode.blockSignals(True)
                self.mode.removeItem(i)
                self.mode.setCurrentIndex(self._saved_mode_index)
                self.mode.blockSignals(False)
                break
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress_label.setText("Готово к отправке")
        self._apply_sending_state()

    def _apply_sending_state(self) -> None:
        sending = self._sending
        paused = self._paused
        mode_data = self.mode.currentData()
        is_channel = isinstance(mode_data, str) and mode_data.startswith("ch:")

        self.message.setReadOnly(sending)
        self.mode.setEnabled(not sending)
        self.channel.setEnabled(not sending and not is_channel)

        if not sending:
            self.start_btn.setText("Старт")
            self.start_btn.setEnabled(True)
            self.start_btn.setToolTip("")
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
        elif paused:
            self.start_btn.setText("Продолжить")
            self.start_btn.setEnabled(self._connected)
            if self._connected:
                self.start_btn.setToolTip("")
            else:
                self.start_btn.setToolTip(
                    "Сначала подключите базовую ноду — рассылка продолжится "
                    "с того же получателя"
                )
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        else:
            self.start_btn.setText("Старт")
            self.start_btn.setEnabled(False)
            self.pause_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)

    # --------------------------------------------------------------- handlers

    def _on_start_clicked(self) -> None:
        if self._sending and self._paused:
            self._paused = False
            self._apply_sending_state()
            self.resume_current_requested.emit()
            return
        self.start_broadcast()

    def _on_mode_changed(self, index: int) -> None:
        mode_data = self.mode.currentData()
        is_channel = isinstance(mode_data, str) and mode_data.startswith("ch:")

        if not self._sending:
            self.channel.setEnabled(not is_channel)
            self.badge_table.setEnabled(mode_data == "dm")

        if is_channel:
            try:
                ch_idx = int(mode_data.split(":", 1)[1])
            except Exception:
                ch_idx = None
            if ch_idx is not None:
                self._select_channel_in_combo(ch_idx)
        else:
            # Вернулись в DM — восстанавливаем канал, который был до ухода.
            if self._saved_dm_channel is not None:
                self._select_channel_in_combo(self._saved_dm_channel)

    def _on_any_ack_toggled(self, state: int) -> None:
        self._any_ack = bool(state)
        settings = self._settings_store.load({})
        if not isinstance(settings, dict):
            settings = {}
        settings["any_ack"] = self._any_ack
        self._settings_store.save(settings)
        self.any_ack_changed.emit(self._any_ack)
        self.log(
            "Режим ACK: "
            + ("от любой ноды" if self._any_ack else "только от получателя"),
            "info",
        )

    def _toggle_conn_controls(self) -> None:
        showing = self.status_text.isVisible()
        for w in self._conn_controls:
            w.setVisible(not showing)

    def _set_plug_icon(self, state: str) -> None:
        icons = _ensure_arrow_files()
        path = {
            "off": icons["plug_off"],
            "on": icons["plug_on"],
            "connecting": icons["plug_conn"],
        }.get(state, icons["plug_off"])
        self.plug_btn.setIcon(QIcon(path))

    def toggle_connection(self) -> None:
        if self.connect_button.text() == "Подключить":
            self._set_plug_icon("connecting")
            self.connect_requested.emit(self.port_combo.currentData() or "")
        else:
            self.disconnect_requested.emit()

    def set_connected(self, connected: bool, text: str) -> None:
        self._connected = bool(connected)

        if connected:
            node_num = ""
            if "Node" in text:
                node_num = text.split("Node", 1)[1].strip()
            display = (
                f"Подключена нода {node_num}" if node_num
                else "Подключена нода"
            )
        else:
            display = "Нода не подключена"

        self.status_text.setText(display)
        self.status_text.setProperty("connected", connected)
        self.status_text.style().unpolish(self.status_text)
        self.status_text.style().polish(self.status_text)
        self.connect_button.setText("Отключить" if connected else "Подключить")
        self._set_plug_icon("on" if connected else "off")
        self._apply_sending_state()

    def set_ports(self, ports: list) -> None:
        self.port_combo.clear()
        self.port_combo.addItem("AUTO", "")
        for p in ports:
            self.port_combo.addItem(p, p)

    def reload_badges(self) -> None:
        parsed: list[tuple[str, str, str]] = []
        try:
            with self.badges_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        parsed.append((parts[0], parts[1], parts[2]))
                    elif len(parts) == 1:
                        parsed.append(("", "", parts[0]))
        except FileNotFoundError:
            self.log("badges_list.txt не найден", "error")
        except Exception as exc:
            self.log(f"Ошибка чтения badges_list.txt: {exc}", "error")

        self.badges = parsed
        valid_ids = {x[2] for x in parsed}
        self.selected.intersection_update(valid_ids)

        self._loading_badges = True
        try:
            self.badge_table.setRowCount(0)
            for name, password, node_id in parsed:
                row = self.badge_table.rowCount()
                self.badge_table.insertRow(row)

                name_item = QTableWidgetItem(name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.badge_table.setItem(row, 0, name_item)

                node_item = QTableWidgetItem(node_id)
                node_item.setFlags(node_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.badge_table.setItem(row, 1, node_item)

                self.badge_table.setCellWidget(row, 2, PasswordWidget(password))

                self.badge_table.setItem(
                    row, 3, QTableWidgetItem(self.notes.get(node_id, ""))
                )

                cb = CenteredCheckBox(node_id in self.selected)
                cb.stateChanged.connect(
                    lambda state, n=node_id: self._set_selected(n, bool(state))
                )
                self.badge_table.setCellWidget(row, 4, cb)
        finally:
            self._loading_badges = False
        self.badge_count.setText(f"{len(parsed)} бейджиков")

    def _set_selected(self, node_id: str, checked: bool) -> None:
        if checked:
            self.selected.add(node_id)
        else:
            self.selected.discard(node_id)

    def select_all(self) -> None:
        self.selected = {x[2] for x in self.badges}
        self.reload_badges()

    def clear_selection(self) -> None:
        self.selected.clear()
        self.reload_badges()

    # --------------------------------------------------------- counters

    def update_counter(self) -> None:
        if self._msg_trimming:
            return
        text = self.message.toPlainText()
        data = text.encode("utf-8")

        if len(data) > MAX_TEXT_BYTES:
            new_text = self._truncate_to_bytes(text, MAX_TEXT_BYTES)
            pos = self.message.textCursor().position()
            self._msg_trimming = True
            try:
                self.message.setPlainText(new_text)
                cursor = self.message.textCursor()
                cursor.setPosition(min(pos, len(new_text)))
                self.message.setTextCursor(cursor)
            finally:
                self._msg_trimming = False
            data = new_text.encode("utf-8")

        n = len(data)
        self.counter.setText(f"{n} / {MAX_TEXT_BYTES} байт")
        self.counter.setStyleSheet(
            "color:#ff4d67;" if n >= MAX_TEXT_BYTES else ""
        )

    @staticmethod
    def _truncate_to_bytes(text: str, limit: int) -> str:
        data = text.encode("utf-8")
        if len(data) <= limit:
            return text
        cut = data[:limit]
        while cut:
            try:
                return cut.decode("utf-8")
            except UnicodeDecodeError:
                cut = cut[:-1]
        return ""

    @staticmethod
    def _fmt_run_at_display(run_at: str) -> str:
        if not run_at:
            return run_at
        return run_at.replace("T", " ", 1)

    def _on_sch_message_changed(self) -> None:
        if self._sch_trimming:
            return
        text = self.sch_message.toPlainText()
        data = text.encode("utf-8")

        if len(data) > MAX_TEXT_BYTES:
            new_text = self._truncate_to_bytes(text, MAX_TEXT_BYTES)
            pos = self.sch_message.textCursor().position()
            self._sch_trimming = True
            try:
                self.sch_message.setPlainText(new_text)
                cursor = self.sch_message.textCursor()
                cursor.setPosition(min(pos, len(new_text)))
                self.sch_message.setTextCursor(cursor)
            finally:
                self._sch_trimming = False
            self.sch_message._recompute()
            data = new_text.encode("utf-8")

        n = len(data)
        self.sch_counter.setText(f"{n} / {MAX_TEXT_BYTES} байт")
        self.sch_counter.setStyleSheet(
            "color:#ff4d67;" if n >= MAX_TEXT_BYTES else ""
        )

    # --------------------------------------------------------- broadcast

    def start_broadcast(self) -> None:
        text = self.message.toPlainText().strip()
        mode_data = self.mode.currentData()

        if isinstance(mode_data, str) and mode_data.startswith("ch:"):
            try:
                ch_idx = int(mode_data.split(":", 1)[1])
            except Exception:
                ch_idx = DEFAULT_CHANNEL_INDEX
            ch_name = self._channel_names.get(ch_idx, "")
            if not ch_name:
                QMessageBox.warning(
                    self, "Ошибка",
                    f"Канал {ch_idx} отсутствует на ноде.",
                )
                return
            recipients_payload = [f"@channel:{ch_name}"]
            total = 1
            channel_idx = ch_idx
        else:
            recipients = list(self.selected)
            if not recipients:
                QMessageBox.warning(
                    self, "Нет получателей", "Выберите хотя бы одного бейджика."
                )
                return
            recipients_payload = recipients
            total = len(recipients)

            cd = self.channel.currentData()
            if cd is None:
                if self._connected:
                    QMessageBox.warning(
                        self, "Нет каналов",
                        "На ноде нет доступных каналов для отправки.",
                    )
                else:
                    QMessageBox.warning(
                        self, "Канал не выбран",
                        "Сначала подключитесь к базовой ноде.",
                    )
                return
            try:
                channel_idx = int(cd)
            except Exception:
                QMessageBox.warning(self, "Ошибка", "Некорректный канал.")
                return

        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.set_sending(scheduled=False)
        self.start_requested.emit(
            text, json.dumps(recipients_payload), channel_idx
        )

    def update_progress(self, current: int, total: int, node_id: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.progress_label.setText(f"{current}/{total} | последний: {node_id}")

    # --------------------------------------------------------- schedule

    def _on_add_schedule_clicked(self) -> None:
        msg = self.sch_message.toPlainText().strip()
        if not msg:
            QMessageBox.warning(self, "Ошибка", "Введите сообщение.")
            return
        n = len(msg.encode("utf-8"))
        if n > MAX_TEXT_BYTES:
            QMessageBox.warning(
                self,
                "Слишком длинное сообщение",
                f"{n} байт, максимум {MAX_TEXT_BYTES} байт.",
            )
            return
        raw = [x.strip() for x in self.sch_recipients.text().split(",") if x.strip()]
        # «*» хранится как есть и разворачивается в момент запуска задачи.
        recipients = raw if raw else ["*"]
        date_str = self.sch_date.dateTime().toString("yyyy-MM-dd")
        time_str = self.sch_time.dateTime().toString("HH:mm:ss")
        run_at = f"{date_str}T{time_str}"
        self.schedule_add.emit(
            msg,
            json.dumps(recipients),
            run_at,
            True,
        )
        self.sch_message.clear()
        self.log(f"Задача добавлена на {run_at}", "success")

    def set_schedule(self, tasks: list) -> None:
        self._loading_schedule = True
        try:
            self._schedule_cache = {}
            self.schedule_table.setRowCount(0)
            for task in tasks:
                self._schedule_cache[task.task_id] = {
                    "message": task.message,
                    "recipients": list(task.recipients),
                    "run_at": task.run_at,
                    "enabled": bool(task.enabled),
                }
                row = self.schedule_table.rowCount()
                self.schedule_table.insertRow(row)
                id_item = QTableWidgetItem(task.task_id)
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if task.last_run:
                    id_item.setToolTip(f"Последний запуск: {task.last_run}")
                self.schedule_table.setItem(row, 0, id_item)
                self.schedule_table.setItem(row, 1, QTableWidgetItem(task.message))
                self.schedule_table.setItem(
                    row, 2, QTableWidgetItem(", ".join(task.recipients))
                )
                self.schedule_table.setItem(
                    row, 3,
                    QTableWidgetItem(self._fmt_run_at_display(task.run_at)),
                )
                cb = CenteredCheckBox(bool(task.enabled))
                cb.setToolTip("Задача активна")
                cb.stateChanged.connect(
                    lambda state, tid=task.task_id:
                        self._on_schedule_enabled_toggled(tid, bool(state))
                )
                self.schedule_table.setCellWidget(row, 4, cb)
        finally:
            self._loading_schedule = False

    def _on_schedule_enabled_toggled(self, task_id: str, enabled: bool) -> None:
        if self._loading_schedule:
            return
        cached = self._schedule_cache.get(task_id)
        if not cached:
            return
        cached["enabled"] = enabled
        self.schedule_edit.emit(
            task_id,
            cached.get("message", ""),
            json.dumps(cached.get("recipients", ["*"])),
            cached.get("run_at", ""),
            enabled,
        )

    def _revert_schedule_cell(self, row: int, col: int, value: str) -> None:
        self._loading_schedule = True
        try:
            item = self.schedule_table.item(row, col)
            if item is not None:
                item.setText(value)
        finally:
            self._loading_schedule = False

    def _on_schedule_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_schedule:
            return
        row = item.row()
        id_item = self.schedule_table.item(row, 0)
        msg_item = self.schedule_table.item(row, 1)
        rec_item = self.schedule_table.item(row, 2)
        run_item = self.schedule_table.item(row, 3)
        if not (id_item and msg_item and rec_item and run_item):
            return

        task_id = id_item.text()
        cached = self._schedule_cache.get(task_id, {})

        message = msg_item.text()
        if not message.strip():
            self.log(
                f"Пустое сообщение. Изменение задачи {task_id} отменено.",
                "error",
            )
            self._revert_schedule_cell(row, 1, cached.get("message", ""))
            return
        n = len(message.encode("utf-8"))
        if n > MAX_TEXT_BYTES:
            self.log(
                f"Сообщение {n} байт — превышает лимит {MAX_TEXT_BYTES}. "
                f"Изменение задачи {task_id} отменено.",
                "error",
            )
            self._revert_schedule_cell(row, 1, cached.get("message", ""))
            return

        run_at = run_item.text().strip().replace(" ", "T")
        try:
            datetime.fromisoformat(run_at)
        except ValueError:
            self.log(
                f"Некорректная дата/время: {run_item.text().strip()!r}. "
                f"Изменение задачи {task_id} отменено.",
                "error",
            )
            self._revert_schedule_cell(
                row, 3,
                self._fmt_run_at_display(cached.get("run_at", "")),
            )
            return

        rec_text = rec_item.text().strip()
        if rec_text == "*":
            recipients = ["*"]
        else:
            recipients = [x.strip() for x in rec_text.split(",") if x.strip()]
        if not recipients:
            recipients = ["*"]

        self.schedule_edit.emit(
            task_id,
            message,
            json.dumps(recipients),
            run_at,
            bool(cached.get("enabled", True)),
        )

    def run_schedule_now(self) -> None:
        row = self.schedule_table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Выполнить немедленно", "Выберите задачу в таблице."
            )
            return
        item = self.schedule_table.item(row, 0)
        if item:
            self.schedule_run_now.emit(item.text())

    def delete_schedule(self) -> None:
        row = self.schedule_table.currentRow()
        if row >= 0:
            item = self.schedule_table.item(row, 0)
            if item:
                self.schedule_delete.emit(item.text())

    def log(self, text: str, level: str = "info") -> None:
        prefix = {
            "success": "[OK]",
            "error": "[ERR]",
            "warning": "[!]",
            "info": "[i]",
        }.get(level, "[i]")
        self.log_view.appendPlainText(f"{prefix} {text}")

    def show_resume(self, state: dict) -> bool:
        recipients = state.get("recipients", [])
        broadcast = state.get("broadcast_channel", "")
        phase = state.get("phase", "first")
        if broadcast:
            target = f"канал {broadcast}"
        elif phase == "second":
            nd = state.get("not_delivered", []) or []
            target = f"повтор по {len(nd)} неподтверждённым"
        else:
            idx = int(state.get("next_index", 0))
            target = recipients[idx] if idx < len(recipients) else "-"
        msg = f"Обнаружена прерванная рассылка.\nПродолжить с {target}?"
        return (
            QMessageBox.question(
                self,
                "Восстановление рассылки",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )