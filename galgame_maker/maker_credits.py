"""Render and import scrolling credits from the maker."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QFont, QImage, QPainter, QPalette,
    QTextDocument, QTextOption,
)
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .project import import_asset, resolve_asset_path

WIDTH, HEIGHT = 1280, 720
PRESETS = {
    "经典黑底": (40, 72, "#FFFFFF", "#101010"),
    "暖色报幕": (45, 56, "#FFE1AA", "#281824"),
}


def find_ffmpeg() -> str | None:
    if getattr(sys, "frozen", False):
        bundle = Path(sys._MEIPASS) / "ffmpeg"
        for directory in (Path(sys.executable).parent, bundle / "bin", bundle):
            candidate = directory / "ffmpeg.exe"
            if candidate.is_file():
                return str(candidate)
    return shutil.which("ffmpeg")


def render_credits(text: str, size: int, color: QColor, image_path: Path) -> int:
    # Qt lays out Unicode, applies system font fallback and wraps long names
    # before encoding. The FFmpeg filter only handles a PNG and numeric values.
    document = QTextDocument()
    font = QFont()
    font.setPixelSize(size)
    document.setDefaultFont(font)
    option = QTextOption(Qt.AlignmentFlag.AlignHCenter)
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    document.setPlainText(text)
    document.setTextWidth(WIDTH - 128)
    height = int(document.size().height()) + 1
    if height > 15000:
        raise ValueError("制作人名单过长，请减少行数或字号")
    image = QImage(int(document.textWidth()), height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    context = QAbstractTextDocumentLayout.PaintContext()
    context.palette.setColor(QPalette.ColorRole.Text, color)
    try:
        document.documentLayout().draw(painter, context)
    finally:
        painter.end()
    if not image.save(str(image_path)):
        raise OSError("无法生成片尾字幕图")
    return height


def encode_arguments(
    image: Path, music: Path | None, output: Path, height: int, speed: int, background: QColor,
) -> list[str]:
    duration = (HEIGHT + height) / speed + 1
    arguments = [
        "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=0x{background.name()[1:]}:s={WIDTH}x{HEIGHT}:r=30",
        "-i", str(image),
    ]
    if music is not None:
        arguments += ["-i", str(music)]
    arguments += [
        "-filter_complex", f"[0:v][1:v]overlay=(W-w)/2:{HEIGHT}-t*{speed}[video]",
        "-map", "[video]", "-t", f"{duration:.3f}", "-c:v", "libx264",
        "-preset", "fast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ]
    if music is not None:
        arguments += ["-map", "2:a:0", "-af", "apad", "-c:a", "aac", "-b:a", "192k"]
    return [*arguments, str(output)]


class CreditsDialog(QDialog):
    def __init__(self, parent: QWidget, root: Path, project: dict):
        super().__init__(parent)
        self.root = root
        self.project = project
        self.asset_id: str | None = None
        self.temporary: TemporaryDirectory | None = None
        self.cancelling = False
        self.process = QProcess(self)
        self.process.finished.connect(self.finish_encode)
        self.process.errorOccurred.connect(self.process_error)
        self.setWindowTitle("生成片尾报幕")
        self.resize(540, 470)

        layout = QVBoxLayout(self)
        self.inputs = QWidget()
        form = QFormLayout(self.inputs)
        self.name = QLineEdit("片尾报幕")
        self.names = QPlainTextEdit()
        self.names.setPlaceholderText("输入制作人名单，每行一项")
        self.music = QComboBox()
        self.music.addItem("无片尾曲", None)
        for asset in project["assets"]:
            if asset["kind"] == "bgm":
                self.music.addItem(asset["name"], asset)
        self.preset = QComboBox()
        self.preset.addItems([*PRESETS, "自定义"])
        self.size = QSpinBox()
        self.size.setRange(16, 100)
        self.speed = QSpinBox()
        self.speed.setRange(24, 240)
        self.speed.setSuffix(" px/s")
        self.foreground = QLineEdit()
        self.background = QLineEdit()
        for label, widget in (
            ("视频名称", self.name), ("制作人名单", self.names), ("片尾曲", self.music),
            ("滚动风格", self.preset), ("字号", self.size), ("滚动速度", self.speed),
            ("文字颜色", self.foreground), ("背景颜色", self.background),
        ):
            form.addRow(label, widget)
        self.preset.currentTextChanged.connect(self.apply_preset)
        self.apply_preset(self.preset.currentText())
        layout.addWidget(self.inputs)

        self.status = QLabel()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.generate = QPushButton("生成并导入")
        self.generate.clicked.connect(self.start_encode)
        self.cancel = QPushButton("取消")
        self.cancel.clicked.connect(self.reject)
        for widget in (self.status, self.progress, self.generate, self.cancel):
            layout.addWidget(widget)

    def apply_preset(self, name: str):
        custom = name not in PRESETS
        for widget in (self.size, self.speed, self.foreground, self.background):
            widget.setEnabled(custom)
        if not custom:
            size, speed, foreground, background = PRESETS[name]
            self.size.setValue(size)
            self.speed.setValue(speed)
            self.foreground.setText(foreground)
            self.background.setText(background)

    def start_encode(self):
        names = self.names.toPlainText().strip()
        name = self.name.text().strip()
        foreground = QColor(self.foreground.text().strip())
        background = QColor(self.background.text().strip())
        if not name or not names:
            QMessageBox.warning(self, "生成片尾", "请输入视频名称和制作人名单")
            return
        if not foreground.isValid() or not background.isValid():
            QMessageBox.warning(self, "生成片尾", "请输入有效颜色，例如 #FFFFFF")
            return
        executable = find_ffmpeg()
        if executable is None:
            QMessageBox.warning(self, "生成片尾", "找不到 FFmpeg。请将 ffmpeg.exe 放在制作器旁或加入 PATH。")
            return

        music = self.music.currentData()
        try:
            music_path = resolve_asset_path(self.root, music["path"]) if music else None
            self.temporary = TemporaryDirectory(prefix="galgame-credits-")
            folder = Path(self.temporary.name)
            image = folder / "credits.png"
            height = render_credits(names, self.size.value(), foreground, image)
            self.video_path = folder / "credits.mp4"
            arguments = encode_arguments(image, music_path, self.video_path, height, self.speed.value(), background)
        except (OSError, ValueError) as error:
            self.reset_job()
            QMessageBox.warning(self, "生成片尾失败", str(error))
            return
        self.settings = {
            "names": names, "music_asset_id": music["id"] if music else None,
            "preset": self.preset.currentText(), "font_size": self.size.value(),
            "speed": self.speed.value(), "foreground": foreground.name(), "background": background.name(),
        }
        self.inputs.setEnabled(False)
        self.generate.setEnabled(False)
        self.progress.show()
        self.status.setText("正在生成片尾视频…")
        self.process.start(executable, arguments)

    def finish_encode(self, exit_code, exit_status):
        if self.cancelling:
            self.reset_job()
            super().reject()
            return
        try:
            if exit_code or exit_status != QProcess.ExitStatus.NormalExit:
                detail = self.process.readAllStandardError().data().decode("utf-8", errors="replace").strip()
                raise OSError(detail or f"FFmpeg 退出码：{exit_code}")
            asset = import_asset(self.root, self.project, self.video_path, "video", self.name.text().strip())
            self.asset_id = asset["id"]
            self.project.setdefault("editor", {}).setdefault("generated_credits", {})[self.asset_id] = self.settings
        except (OSError, ValueError) as error:
            self.reset_job()
            QMessageBox.warning(self, "生成片尾失败", str(error))
            return
        self.reset_job()
        self.accept()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.reset_job()
            if self.cancelling:
                super().reject()
            else:
                QMessageBox.warning(self, "无法启动 FFmpeg", self.process.errorString())

    def reset_job(self):
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
        self.inputs.setEnabled(True)
        self.generate.setEnabled(True)
        self.progress.hide()
        self.status.clear()

    def reject(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.cancelling = True
            self.process.kill()
            return
        super().reject()
