"""Interactive test of the packaged Qt Multimedia backend."""

import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget


class MediaProbe(QWidget):
    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.end_events = 0
        self.video_frames = 0
        self.credits_name = None
        self.last_bgm_position = 0
        self.setWindowTitle("GalGame Maker 媒体验证")
        self.resize(720, 560)
        self.layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.layout.addWidget(self.status)
        self.video = QVideoWidget(self)
        self.layout.addWidget(self.video, 1)
        self.video.hide()

        self.bgm = self.make_player("BGM", 0.2)
        self.bgm.setLoops(QMediaPlayer.Loops.Infinite)
        self.bgm.positionChanged.connect(self.observe_loop)
        self.effect = self.make_player("音效", 0.55)
        self.effect.playbackStateChanged.connect(self.observe_overlap)
        self.credits = self.make_player("片尾", 0.3)
        self.credits.setVideoOutput(self.video)
        self.video.videoSink().videoFrameChanged.connect(self.count_frame)
        self.credits.mediaStatusChanged.connect(self.credits_status_changed)
        self.credits.errorOccurred.connect(self.credits_failed)
        self.title_buttons = []
        for caption, action in (
            ("循环播放 MP3 背景音乐", self.play_bgm),
            ("播放 WAV 音效", lambda: self.play(self.effect, "effect.wav")),
            ("停止背景音乐", self.bgm.stop),
            ("播放有声片尾", lambda: self.play_credits("credits.mp4")),
            ("播放无声片尾", lambda: self.play_credits("silent.mp4")),
            ("播放损坏文件", lambda: self.play_credits("broken.mp4")),
        ):
            button = QPushButton(caption)
            button.clicked.connect(action)
            self.layout.addWidget(button)
            self.title_buttons.append(button)
        self.leave = QPushButton("返回标题")
        self.leave.clicked.connect(self.return_to_title)
        self.leave.hide()
        self.layout.addWidget(self.leave)
        self.log(f"标题就绪 | 输出设备 {QMediaDevices.defaultAudioOutput().description()}")

    def log(self, message):
        with (self.root / "probe.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().isoformat(timespec='milliseconds')} {message}\n")
        self.status.setText(message)

    def make_player(self, label, volume):
        player = QMediaPlayer(self)
        audio = QAudioOutput(player)
        audio.setVolume(volume)
        player.setAudioOutput(audio)
        player.playbackStateChanged.connect(lambda state: self.log(f"{label} | {state.name}"))
        player.errorOccurred.connect(lambda error, detail: self.log(f"{label} | {error.name} | {detail}"))
        return player

    def play(self, player, name):
        player.setSource(QUrl.fromLocalFile(str(self.root / "media" / name)))
        player.setPosition(0)
        player.play()

    def play_bgm(self):
        self.last_bgm_position = 0
        self.play(self.bgm, "bgm.mp3")

    def observe_loop(self, position):
        if (
            self.bgm.isPlaying()
            and self.last_bgm_position >= 1500
            and position < 500
        ):
            self.log("BGM | 已回到曲首")
        self.last_bgm_position = position

    def observe_overlap(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.log(f"音效 | 播放开始时 BGM 播放中 {self.bgm.isPlaying()}")

    def play_credits(self, name):
        self.bgm.stop()
        self.effect.stop()
        self.end_events = 0
        self.video_frames = 0
        self.credits_name = name
        for button in self.title_buttons:
            button.hide()
        self.video.show()
        self.play(self.credits, name)

    def count_frame(self, frame):
        if frame.isValid():
            self.video_frames += 1

    def credits_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.end_events += 1
            self.log(
                f"{self.credits_name} | 结束次数 {self.end_events} | "
                f"有效帧 {self.video_frames} | 音轨 {self.credits.hasAudio()}"
            )
            self.leave.show()

    def credits_failed(self, error, detail):
        self.log(f"{self.credits_name} | 播放失败 | {error.name} | {detail}")
        self.leave.show()

    def return_to_title(self):
        self.credits.setSource(QUrl())
        self.video.hide()
        self.leave.hide()
        for button in self.title_buttons:
            button.show()
        self.log("已返回标题")


def main():
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    # Keep the deployment backend explicit and preserve its runtime messages in
    # the probe log, including the FFmpeg version printed at initialization.
    os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"
    os.environ["QT_LOGGING_RULES"] = "qt.multimedia.*.debug=true"
    from PySide6.QtCore import qInstallMessageHandler

    def record_qt_message(mode, context, message):
        if context.category and context.category.startswith("qt.multimedia"):
            with (root / "probe.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{context.category} | {message}\n")

    qInstallMessageHandler(record_qt_message)
    app = QApplication(sys.argv)
    window = MediaProbe(root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
