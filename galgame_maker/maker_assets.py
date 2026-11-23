"""Maker-side folder-organized content browser with thumbnails and media preview."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSlider, QSplitter, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .project import (
    MUSIC_KINDS, SPRITE_DIMENSIONS, import_asset, replace_asset, resolve_asset_path, verify_assets,
)


EXTENSION_KIND = {
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".txt": "text", ".mp3": "bgm", ".wav": "sfx", ".mp4": "video",
}

IMAGE_CATEGORIES = ("背景", "立绘", "界面", "其他")

KIND_LABELS = {
    "image": "图片",
    "bgm": "背景音乐",
    "song": "人声歌曲",
    "sfx": "音效",
    "voice": "角色语音",
    "video": "片尾视频",
    "text": "文本资料",
}
AUDIO_KINDS = {"sfx", "voice", *MUSIC_KINDS}


def variant_description(asset: dict, project: dict) -> str:
    variant = asset.get("sprite_variant")
    if variant is None:
        return ""
    character = next(
        (entry["name"] for entry in project["characters"] if entry["id"] == variant["character_id"]),
        "",
    )
    return " · ".join(filter(None, [character, *(variant[key] for key in SPRITE_DIMENSIONS)]))


def infer_image_tag(name: str) -> str:
    lower = name.casefold()
    if lower.startswith(("bg", "background")) or "背景" in name:
        return "背景"
    if lower.startswith(("char", "sprite", "ch_")) or "立绘" in name or "角色" in name:
        return "立绘"
    if lower.startswith(("ui", "btn", "box", "menu")) or "界面" in name or "框" in name:
        return "界面"
    return "其他"


def _format_ms(ms: int) -> str:
    seconds = max(0, ms // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _make_badge_icon(kind: str) -> QIcon:
    """Draw a clean vector card icon for non-image assets."""
    colors = {
        "bgm": ("#2C224A", "#9F7AEA", "BGM"),
        "song": ("#44263A", "#F3A6C8", "SONG"),
        "sfx": ("#1B3B3A", "#38B2AC", "SFX"),
        "voice": ("#3E2537", "#EBA7D1", "VOICE"),
        "video": ("#3E2A1A", "#ED8936", "MP4"),
        "text": ("#1B2E46", "#63B3ED", "TXT"),
        "image": ("#232936", "#94A3B8", "IMG"),
    }
    bg, accent, label = colors.get(kind, ("#232936", "#94A3B8", kind.upper()))
    pix = QPixmap(128, 96)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QColor(bg))
    p.setPen(QPen(QColor(accent), 2))
    p.drawRoundedRect(QRectF(4, 4, 120, 88), 10, 10)
    font = QFont()
    font.setPixelSize(22)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor(accent))
    p.drawText(QRectF(4, 10, 120, 56), Qt.AlignmentFlag.AlignCenter, label)
    sub = QFont()
    sub.setPixelSize(11)
    p.setFont(sub)
    p.setPen(QColor("#CBD5E1"))
    p.drawText(QRectF(4, 62, 120, 22), Qt.AlignmentFlag.AlignCenter, KIND_LABELS.get(kind, kind))
    p.end()
    return QIcon(pix)


def _make_image_thumb(path: Path) -> QIcon:
    """Render an aspect-preserving thumbnail centered on a subtle dark frame."""
    source = QPixmap(str(path))
    if source.isNull():
        return _make_badge_icon("image")
    canvas = QPixmap(128, 96)
    canvas.fill(QColor("#11141B"))
    scaled = source.scaled(
        122, 90, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    p = QPainter(canvas)
    p.drawPixmap((128 - scaled.width()) // 2, (96 - scaled.height()) // 2, scaled)
    p.setPen(QPen(QColor("#2F384A"), 1))
    p.drawRect(0, 0, 127, 95)
    p.end()
    return QIcon(canvas)


class AssetPickerDialog(QDialog):
    """Category-filtered, searchable asset picker with thumbnail preview."""

    def __init__(
        self,
        project: dict,
        root: Path,
        required_kind: str,
        title: str = "选择素材",
        default_tag: str | None = None,
        allow_none_label: str | None = None,
        current_asset_id: str | None = None,
        parent=None,
        *,
        sprite_variants: bool = False,
    ):
        super().__init__(parent)
        self.project = project
        self.root = root
        self.required_kind = required_kind
        self.allow_none_label = allow_none_label
        self.selected_asset_id: str | None = current_asset_id
        self.setWindowTitle(title)
        self.resize(620, 440)

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.category = QComboBox()
        if required_kind == "image":
            self.category.addItem("全部分类")
            seen_tags = list(IMAGE_CATEGORIES)
            for asset in project["assets"]:
                if asset["kind"] == "image":
                    for tag in asset.get("tags", []):
                        if tag not in seen_tags:
                            seen_tags.append(tag)
            for tag in seen_tags:
                self.category.addItem(tag)
            if default_tag and self.category.findText(default_tag) >= 0:
                has_match = any(
                    asset["kind"] == "image"
                    and default_tag in (asset.get("tags") or [infer_image_tag(asset["name"])])
                    for asset in project["assets"]
                )
                if has_match:
                    self.category.setCurrentText(default_tag)
            self.category.currentTextChanged.connect(self._reload)
            filters.addWidget(QLabel("分类"))
            filters.addWidget(self.category)
        elif required_kind == "bgm":
            for kind in MUSIC_KINDS:
                self.category.addItem(KIND_LABELS[kind], kind)
            music_assets = [asset for asset in project["assets"] if asset["kind"] in MUSIC_KINDS]
            selected = next((asset for asset in music_assets if asset["id"] == current_asset_id), None)
            if selected is not None or (music_assets and all(asset["kind"] == "song" for asset in music_assets)):
                self.category.setCurrentIndex(self.category.findData(selected["kind"] if selected else "song"))
            self.category.currentIndexChanged.connect(self._reload)
            filters.addWidget(QLabel("音乐类别"))
            filters.addWidget(self.category)
        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称搜索素材...")
        self.search.textChanged.connect(self._reload)
        filters.addWidget(self.search, 1)
        layout.addLayout(filters)
        self.variant_filters = {}
        if sprite_variants:
            row = QHBoxLayout()
            variants = [asset["sprite_variant"] for asset in project["assets"] if "sprite_variant" in asset]
            role = QComboBox()
            role.addItem("全部角色", None)
            used_ids = {variant["character_id"] for variant in variants}
            for character in project["characters"]:
                if character["id"] in used_ids:
                    role.addItem(character["name"], character["id"])
            self.variant_filters["character_id"] = role
            for field, name in SPRITE_DIMENSIONS.items():
                combo = QComboBox()
                combo.addItem(f"全部{name}", None)
                for value in sorted({variant[field] for variant in variants if variant[field]}):
                    combo.addItem(value, value)
                self.variant_filters[field] = combo
            for combo in self.variant_filters.values():
                row.addWidget(combo)
                combo.currentIndexChanged.connect(self._reload)
            layout.addLayout(row)

        body = QHBoxLayout()
        self.listing = QListWidget()
        self.listing.currentItemChanged.connect(self._update_preview)
        self.listing.itemDoubleClicked.connect(lambda *_: self.accept())
        body.addWidget(self.listing, 1)

        self.preview_label = QLabel("无预览")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedSize(230, 230)
        self.preview_label.setStyleSheet("background: #12151C; color: #9AA6B8; border-radius: 6px;")
        if required_kind == "image":
            body.addWidget(self.preview_label)
        layout.addLayout(body, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._reload(select_id=current_asset_id)

    def _reload(self, *_args, select_id: str | None = None):
        target_id = select_id if select_id is not None else self.current_choice()
        self.listing.clear()
        if self.allow_none_label is not None:
            none_row = QListWidgetItem(self.allow_none_label)
            none_row.setData(Qt.ItemDataRole.UserRole, None)
            self.listing.addItem(none_row)
        needle = self.search.text().casefold().strip()
        selected_cat = self.category.currentText() if self.required_kind == "image" else "全部分类"
        selected_kind = self.category.currentData() if self.required_kind == "bgm" else self.required_kind
        required_fields = {
            key: combo.currentData() for key, combo in self.variant_filters.items()
            if combo.currentData() is not None
        }
        for asset in self.project["assets"]:
            if asset["kind"] != selected_kind:
                continue
            tags = asset.get("tags", [])
            if self.required_kind == "image" and selected_cat != "全部分类":
                inferred = tags or [infer_image_tag(asset["name"])]
                if selected_cat not in inferred:
                    continue
            variant = asset.get("sprite_variant", {})
            if any(variant.get(key) != value for key, value in required_fields.items()):
                continue
            detail = variant_description(asset, self.project)
            if needle and needle not in f"{asset['name']} {detail}".casefold():
                continue
            tag_suffix = f" [{'/'.join(tags)}]" if tags else ""
            row = QListWidgetItem(f"{asset['name']}{tag_suffix}")
            if detail:
                row.setText(f"{row.text()} · {detail}")
            row.setData(Qt.ItemDataRole.UserRole, asset["id"])
            self.listing.addItem(row)
            if asset["id"] == target_id:
                self.listing.setCurrentItem(row)
        if self.listing.currentRow() < 0 and self.listing.count():
            self.listing.setCurrentRow(0)
        self._update_preview()

    def current_choice(self) -> str | None:
        row = self.listing.currentItem()
        if row is None:
            return None
        return row.data(Qt.ItemDataRole.UserRole)

    def _update_preview(self, *_):
        if self.required_kind != "image":
            return
        asset_id = self.current_choice()
        if not asset_id:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(self.allow_none_label or "无预览")
            return
        asset = next((a for a in self.project["assets"] if a["id"] == asset_id), None)
        if asset is None:
            return
        pix = QPixmap(str(resolve_asset_path(self.root, asset["path"])))
        if pix.isNull():
            self.preview_label.setText("无法加载图片")
        else:
            self.preview_label.setPixmap(pix.scaled(
                220, 220, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def accept(self):
        if self.listing.currentItem() is None:
            return
        self.selected_asset_id = self.current_choice()
        super().accept()


def asset_references(project: dict, asset_id: str) -> list[str]:
    references = []
    for character in project["characters"]:
        if character.get("reference_art_id") == asset_id:
            references.append(f"角色 {character['name']} 设定立绘")
    for document in project.get("documents", []):
        if document.get("source_asset_id") == asset_id:
            references.append(f"资料 {document['title']}")
    for node in project["nodes"]:
        for item in node["items"]:
            if item.get("voice_asset_id") == asset_id:
                references.append(f"节点 {node['name']} 对白语音")
            elif item.get("asset_id") == asset_id:
                references.append(f"节点 {node['name']} 演出")
        if (node.get("ending") or {}).get("credits_video_asset_id") == asset_id:
            references.append(f"节点 {node['name']} 片尾")
    for part, style in project.get("theme", {}).items():
        if isinstance(style, dict) and style.get("image_asset_id") == asset_id:
            references.append(f"主题 {part}")
    return references


class AssetPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._thumb_cache: dict[tuple[str, str], QIcon] = {}
        self._badge_cache: dict[str, QIcon] = {}
        self._active_folder_tag: str | None = None
        self._seeking = False

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self.player)
        self.audio_output.setVolume(0.8)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)

        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(["全部", *KIND_LABELS])
        self.kind.currentTextChanged.connect(self._on_kind_combo_changed)
        controls.addWidget(self.kind)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索名称、标签或立绘变体...")
        self.query.textChanged.connect(self.refresh)
        controls.addWidget(self.query, 1)
        for caption, action in (
            ("批量导入", self.import_file),
            ("新建分类", self.add_folder),
            ("分类标签", self.edit_tags),
            ("音乐分类", self.edit_music_kind),
            ("立绘变体", self.edit_variant),
            ("替换", self.replace_file),
            ("重命名", self.rename_asset),
            ("移除", self.remove_asset),
            ("校验完整性", self.verify_integrity),
        ):
            button = QPushButton(caption)
            if caption == "批量导入":
                button.setProperty("accent", True)
            button.clicked.connect(action)
            controls.addWidget(button)
        outer.addLayout(controls)

        split = QSplitter()
        outer.addWidget(split, 1)

        # Left pane: Folder/category tree
        folder_box = QWidget()
        folder_layout = QVBoxLayout(folder_box)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_title = QLabel("素材分类目录")
        folder_title.setProperty("sectionTitle", True)
        folder_layout.addWidget(folder_title)
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.currentItemChanged.connect(self._on_folder_selected)
        folder_layout.addWidget(self.folder_tree, 1)
        split.addWidget(folder_box)

        # Center pane: Visual thumbnail grid
        grid_box = QWidget()
        grid_layout = QVBoxLayout(grid_box)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_header = QLabel("素材库（图片直显缩略图，双击音频/视频即可试听）")
        self.grid_header.setProperty("sectionTitle", True)
        grid_layout.addWidget(self.grid_header)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(128, 96))
        self.list.setGridSize(QSize(156, 138))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(6)
        self.list.currentItemChanged.connect(self.preview)
        self.list.itemDoubleClicked.connect(self._on_item_double_clicked)
        grid_layout.addWidget(self.list, 1)
        split.addWidget(grid_box)

        # Right pane: Large preview + Audio/Video player controls + Details
        inspector = QWidget()
        insp_layout = QVBoxLayout(inspector)
        insp_layout.setContentsMargins(6, 0, 0, 0)
        preview_title = QLabel("预览与试听")
        preview_title.setProperty("sectionTitle", True)
        insp_layout.addWidget(preview_title)

        self.image = QLabel("选择素材查看预览")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(220)
        self.image.setStyleSheet("background: #12151C; border: 1px solid #2A3140; border-radius: 6px;")
        insp_layout.addWidget(self.image)

        self.video = QVideoWidget()
        self.video.setMinimumHeight(220)
        self.video.hide()
        self.player.setVideoOutput(self.video)
        insp_layout.addWidget(self.video)

        self.text_preview = QTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setMinimumHeight(200)
        self.text_preview.hide()
        insp_layout.addWidget(self.text_preview)

        # Built-in media playback bar for BGM / SFX / Video
        self.media_controls = QWidget()
        media_layout = QVBoxLayout(self.media_controls)
        media_layout.setContentsMargins(0, 6, 0, 6)
        btn_row = QHBoxLayout()
        self.play_btn = QPushButton("▶ 播放")
        self.play_btn.setProperty("accent", True)
        self.play_btn.clicked.connect(self.play)
        btn_row.addWidget(self.play_btn)
        self.pause_btn = QPushButton("⏸ 暂停")
        self.pause_btn.clicked.connect(self.player.pause)
        btn_row.addWidget(self.pause_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.clicked.connect(self.stop_media)
        btn_row.addWidget(self.stop_btn)
        media_layout.addLayout(btn_row)

        seek_row = QHBoxLayout()
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek_slider.sliderReleased.connect(self._finish_seek)
        seek_row.addWidget(self.seek_slider, 1)
        self.time_label = QLabel("00:00 / 00:00")
        seek_row.addWidget(self.time_label)
        media_layout.addLayout(seek_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("音量"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100.0))
        vol_row.addWidget(self.volume_slider, 1)
        media_layout.addLayout(vol_row)
        self.media_controls.hide()
        insp_layout.addWidget(self.media_controls)

        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setStyleSheet("color: #9FB2C9; padding-top: 6px;")
        insp_layout.addWidget(self.details)
        insp_layout.addStretch()
        split.addWidget(inspector)
        split.setSizes([200, 680, 320])

    def _badge_icon(self, kind: str) -> QIcon:
        if kind not in self._badge_cache:
            self._badge_cache[kind] = _make_badge_icon(kind)
        return self._badge_cache[kind]

    def _asset_icon(self, asset: dict) -> QIcon:
        if asset["kind"] != "image" or self.window.root is None:
            return self._badge_icon(asset["kind"])
        key = (asset["id"], asset.get("sha256", ""))
        if key not in self._thumb_cache:
            try:
                path = resolve_asset_path(self.window.root, asset["path"])
                self._thumb_cache[key] = _make_image_thumb(path)
            except (OSError, ValueError):
                return self._badge_icon("image")
        return self._thumb_cache[key]

    def _rebuild_folder_tree(self):
        current = self.folder_tree.currentItem()
        current_data = current.data(0, Qt.ItemDataRole.UserRole) if current else ("全部", None)
        self.folder_tree.blockSignals(True)
        self.folder_tree.clear()

        assets = self.window.project["assets"] if self.window.project else []
        custom_folders = (
            self.window.project.get("editor", {}).get("asset_folders", {})
            if self.window.project else {}
        )

        root_all = QTreeWidgetItem([f"📁 全部素材 ({len(assets)})"])
        root_all.setData(0, Qt.ItemDataRole.UserRole, ("全部", None))
        self.folder_tree.addTopLevelItem(root_all)

        for kind in KIND_LABELS:
            kind_assets = [a for a in assets if a["kind"] == kind]
            kind_node = QTreeWidgetItem([f"📂 {KIND_LABELS[kind]} ({len(kind_assets)})"])
            kind_node.setData(0, Qt.ItemDataRole.UserRole, (kind, None))
            self.folder_tree.addTopLevelItem(kind_node)

            tags: list[str] = list(IMAGE_CATEGORIES) if kind == "image" else []
            for folder_name in custom_folders.get(kind, []):
                if folder_name not in tags:
                    tags.append(folder_name)
            for asset in kind_assets:
                for tag in asset.get("tags", []):
                    if tag not in tags:
                        tags.append(tag)
            for tag in tags:
                if kind == "image":
                    count = sum(
                        1 for a in kind_assets
                        if tag in (a.get("tags") or [infer_image_tag(a["name"])])
                    )
                else:
                    count = sum(1 for a in kind_assets if tag in a.get("tags", []))
                sub = QTreeWidgetItem([f"  └ {tag} ({count})"])
                sub.setData(0, Qt.ItemDataRole.UserRole, (kind, tag))
                kind_node.addChild(sub)
            kind_node.setExpanded(kind == "image" or kind_node.childCount() > 0)

        # Restore selection
        matched = None
        for i in range(self.folder_tree.topLevelItemCount()):
            top = self.folder_tree.topLevelItem(i)
            if top.data(0, Qt.ItemDataRole.UserRole) == current_data:
                matched = top
                break
            for j in range(top.childCount()):
                child = top.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == current_data:
                    matched = child
                    break
            if matched:
                break
        self.folder_tree.setCurrentItem(matched or root_all)
        self.folder_tree.blockSignals(False)

    def _on_folder_selected(self, current, previous):
        if current is None:
            return
        kind_filter, tag_filter = current.data(0, Qt.ItemDataRole.UserRole)
        self._active_folder_tag = tag_filter
        self.kind.blockSignals(True)
        self.kind.setCurrentText(kind_filter)
        self.kind.blockSignals(False)
        self.refresh(rebuild_tree=False)

    def _on_kind_combo_changed(self, text: str):
        self._active_folder_tag = None
        self.refresh(rebuild_tree=False)

    def add_folder(self):
        if not self.window.require_project():
            return
        current = self.folder_tree.currentItem()
        default_kind = "image"
        if current is not None:
            k, _ = current.data(0, Qt.ItemDataRole.UserRole)
            if k != "全部":
                default_kind = k
        kinds = list(KIND_LABELS)
        kind_labels = [f"{KIND_LABELS[k]} ({k})" for k in kinds]
        selected_label, accepted = QInputDialog.getItem(
            self, "新建分类文件夹", "所属类型", kind_labels, kinds.index(default_kind), editable=False,
        )
        if not accepted:
            return
        kind = kinds[kind_labels.index(selected_label)]
        name, accepted = QInputDialog.getText(self, "新建分类文件夹", "文件夹名称")
        if not accepted or not name.strip():
            return
        folders = self.window.project.setdefault("editor", {}).setdefault("asset_folders", {})
        bucket = folders.setdefault(kind, [])
        if name.strip() not in bucket:
            bucket.append(name.strip())
            self.window.mark_dirty()
        self.refresh()

    def selected_asset(self) -> dict | None:
        row = self.list.currentItem()
        if row is None or self.window.project is None:
            return None
        asset_id = row.data(Qt.ItemDataRole.UserRole)
        return next(
            (asset for asset in self.window.project["assets"] if asset["id"] == asset_id), None
        )

    def select_asset(self, asset_id: str, kind: str):
        self._active_folder_tag = None
        self.kind.setCurrentText(kind)
        self.query.clear()
        self.refresh()
        for index in range(self.list.count()):
            row = self.list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == asset_id:
                self.list.setCurrentItem(row)
                break

    def refresh(self, *_args, rebuild_tree: bool = True):
        if rebuild_tree:
            self._rebuild_folder_tree()
        selected = self.selected_asset()
        selected_id = selected["id"] if selected else None
        self.list.blockSignals(True)
        self.list.clear()
        needle = self.query.text().casefold().strip()
        kind_filter = self.kind.currentText()
        if self.window.project:
            for asset in self.window.project["assets"]:
                if kind_filter != "全部" and asset["kind"] != kind_filter:
                    continue
                tags = asset.get("tags", [])
                detail = variant_description(asset, self.window.project)
                if self._active_folder_tag is not None:
                    effective_tags = (
                        tags or [infer_image_tag(asset["name"])]
                        if asset["kind"] == "image" else tags
                    )
                    if self._active_folder_tag not in effective_tags:
                        continue
                if needle:
                    haystack = f"{asset['name']} {' '.join(tags)} {detail}".casefold()
                    if needle not in haystack:
                        continue
                tag_suffix = f"\n[{'/'.join(tags)}]" if tags else ""
                row = QListWidgetItem(self._asset_icon(asset), f"{asset['name']}{tag_suffix}")
                row.setData(Qt.ItemDataRole.UserRole, asset["id"])
                row.setToolTip(" · ".join(filter(None, (asset["name"], KIND_LABELS[asset["kind"]], detail))))
                self.list.addItem(row)
                if asset["id"] == selected_id:
                    self.list.setCurrentItem(row)
        self.list.blockSignals(False)
        self.preview()

    def import_file(self):
        if not self.window.require_project():
            return
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "导入素材（可多选批量导入）",
            filter="支持的素材 (*.png *.jpg *.jpeg *.txt *.mp3 *.wav *.mp4);;所有文件 (*.*)",
        )
        if not filenames:
            return
        if len(filenames) == 1:
            source = Path(filenames[0])
            suggested = EXTENSION_KIND.get(source.suffix.lower())
            if suggested is None:
                QMessageBox.warning(self, "格式", "首版支持 PNG、JPEG、TXT、MP3、WAV、MP4")
                return
            selected = suggested
            if suggested in AUDIO_KINDS:
                kinds = [kind for kind in KIND_LABELS if kind in AUDIO_KINDS]
                labels = [KIND_LABELS[kind] for kind in kinds]
                preferred = self.kind.currentText()
                default = preferred if preferred in AUDIO_KINDS else suggested
                label, accepted = QInputDialog.getItem(
                    self, "素材用途", "类型", labels, kinds.index(default), editable=False,
                )
                if not accepted:
                    return
                selected = kinds[labels.index(label)]
            name, accepted = QInputDialog.getText(self, "素材名称", "名称", text=source.stem)
            if not accepted or not name.strip():
                return
            tags: list[str] = []
            if selected == "image":
                default_tag = self._active_folder_tag or infer_image_tag(name.strip())
                all_tags = list(IMAGE_CATEGORIES)
                for custom in self.window.project.get("editor", {}).get("asset_folders", {}).get("image", []):
                    if custom not in all_tags:
                        all_tags.append(custom)
                idx = all_tags.index(default_tag) if default_tag in all_tags else 0
                tag_choice, accepted = QInputDialog.getItem(
                    self, "图片分类", "分类文件夹（用于剧情编辑筛选）", all_tags, idx, editable=True,
                )
                if accepted and tag_choice.strip():
                    tags = [tag_choice.strip()]
            elif self._active_folder_tag:
                tags = [self._active_folder_tag]
            try:
                record = import_asset(
                    self.window.root, self.window.project, source, selected, name.strip(),
                )
                if tags:
                    record["tags"] = tags
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "导入失败", str(error))
                return
            last_id = record["id"]
        else:
            has_images = any(
                EXTENSION_KIND.get(Path(f).suffix.lower()) == "image" for f in filenames
            )
            batch_tag = "自动识别"
            if has_images:
                all_tags = ["自动识别", *IMAGE_CATEGORIES]
                for custom in self.window.project.get("editor", {}).get("asset_folders", {}).get("image", []):
                    if custom not in all_tags:
                        all_tags.append(custom)
                choice, accepted = QInputDialog.getItem(
                    self, "批量导入图片分类", "图片分类文件夹", all_tags, 0, editable=True,
                )
                if not accepted:
                    return
                batch_tag = choice.strip() or "自动识别"
            imported: list[dict] = []
            skipped: list[str] = []
            for filename in filenames:
                source = Path(filename)
                kind = EXTENSION_KIND.get(source.suffix.lower())
                if kind in AUDIO_KINDS and self.kind.currentText() in AUDIO_KINDS:
                    kind = self.kind.currentText()
                if kind is None:
                    skipped.append(f"{source.name}（不支持的格式）")
                    continue
                try:
                    record = import_asset(
                        self.window.root, self.window.project, source, kind, source.stem,
                    )
                    if kind == "image":
                        tag = infer_image_tag(source.stem) if batch_tag == "自动识别" else batch_tag
                        record["tags"] = [tag]
                    imported.append(record)
                except (OSError, ValueError) as error:
                    skipped.append(f"{source.name}（{error}）")
            if not imported:
                QMessageBox.warning(self, "导入失败", "\n".join(skipped) or "未导入任何素材")
                return
            last_id = imported[-1]["id"]
            summary = f"已批量导入 {len(imported)} 项素材"
            if skipped:
                summary += f"\n跳过 {len(skipped)} 项：\n" + "\n".join(skipped[:8])
            QMessageBox.information(self, "批量导入完成", summary)

        self.window.mark_dirty()
        self.refresh()
        self.window.character_panel.refresh()
        self.window.theme_panel.load_part()
        for index in range(self.list.count()):
            row = self.list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == last_id:
                self.list.setCurrentItem(row)
                break

    def edit_music_kind(self):
        asset = self.selected_asset()
        if asset is None or asset["kind"] not in MUSIC_KINDS:
            QMessageBox.information(self, "音乐分类", "请选择一项背景音乐或人声歌曲")
            return
        labels = [KIND_LABELS[kind] for kind in MUSIC_KINDS]
        label, accepted = QInputDialog.getItem(
            self, "音乐分类", "用途", labels, MUSIC_KINDS.index(asset["kind"]), editable=False,
        )
        if not accepted:
            return
        kind = MUSIC_KINDS[labels.index(label)]
        if kind != asset["kind"]:
            asset["kind"] = kind
            self.window.mark_dirty()
            self.select_asset(asset["id"], kind)

    def edit_tags(self):
        asset = self.selected_asset()
        if asset is None:
            return
        current = ", ".join(asset.get("tags", []))
        raw, accepted = QInputDialog.getText(
            self, "编辑分类标签 / 文件夹",
            "分类标签（多个用逗号分隔，例如：背景、立绘、战斗音乐）",
            text=current,
        )
        if not accepted:
            return
        tags = [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]
        if tags:
            asset["tags"] = tags
        else:
            asset.pop("tags", None)
        self.window.mark_dirty()
        self.refresh()

    def edit_variant(self):
        asset = self.selected_asset()
        if asset is None or asset["kind"] != "image":
            return
        characters = self.window.project["characters"]
        if not characters:
            QMessageBox.information(self, "立绘变体", "请先创建角色")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑立绘变体")
        form = QFormLayout(dialog)
        previous = asset.get("sprite_variant", {})
        role = QComboBox()
        role.addItem("无角色关联", None)
        for character in characters:
            role.addItem(character["name"], character["id"])
        role.setCurrentIndex(max(0, role.findData(previous.get("character_id"))))
        form.addRow("角色", role)
        inputs = {}
        for key, label in SPRITE_DIMENSIONS.items():
            inputs[key] = QLineEdit(previous.get(key, ""))
            form.addRow(label, inputs[key])
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if role.currentData() is None:
            asset.pop("sprite_variant", None)
        else:
            asset["sprite_variant"] = {"character_id": role.currentData()}
            asset["sprite_variant"].update({key: widget.text().strip() for key, widget in inputs.items()})
            tags = asset.setdefault("tags", [])
            if "立绘" not in tags:
                tags.append("立绘")
        self.window.mark_dirty()
        self.refresh()

    def replace_file(self):
        asset = self.selected_asset()
        if asset is None:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "替换素材文件")
        if not filename:
            return
        source = Path(filename)
        suggested = EXTENSION_KIND.get(source.suffix.lower())
        if suggested is None:
            QMessageBox.warning(self, "格式", "首版支持 PNG、JPEG、TXT、MP3、WAV、MP4")
            return
        if suggested != asset["kind"] and not (asset["kind"] in AUDIO_KINDS and suggested in AUDIO_KINDS):
            QMessageBox.warning(
                self, "类型不匹配",
                f"当前素材类型为 {asset['kind']}，所选文件类型为 {suggested}",
            )
            return
        try:
            self.stop_media()
            replace_asset(self.window.root, self.window.project, asset["id"], source)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "替换失败", str(error))
            return
        self.window.mark_dirty()
        self.refresh()

    def rename_asset(self):
        asset = self.selected_asset()
        if asset is None:
            return
        name, accepted = QInputDialog.getText(self, "重命名素材", "名称", text=asset["name"])
        if not accepted or not name.strip():
            return
        asset["name"] = name.strip()
        self.window.mark_dirty()
        self.refresh()
        self.window.character_panel.refresh()
        self.window.theme_panel.load_part()

    def verify_integrity(self):
        if not self.window.require_project():
            return
        issues = verify_assets(self.window.root, self.window.project)
        if not issues:
            total = len(self.window.project["assets"])
            QMessageBox.information(self, "完整性检查", f"全部 {total} 项素材校验通过")
            return
        lines = [f"{item['name']} ({item['path']}): {item['issue']}" for item in issues]
        QMessageBox.warning(self, "发现异常素材", "\n".join(lines))

    def remove_asset(self):
        asset = self.selected_asset()
        if asset is None:
            return
        references = asset_references(self.window.project, asset["id"])
        if references:
            QMessageBox.warning(self, "素材仍在使用", "\n".join(references))
            return
        self.stop_media()
        self.window.project["assets"].remove(asset)
        self.window.project.get("editor", {}).get("generated_credits", {}).pop(asset["id"], None)
        self.window.mark_dirty()
        self.refresh()
        self.window.character_panel.refresh()
        self.window.theme_panel.load_part()

    def _on_item_double_clicked(self, item: QListWidgetItem):
        asset = self.selected_asset()
        if asset and asset["kind"] in AUDIO_KINDS | {"video"}:
            self.play()

    def _on_position_changed(self, pos: int):
        if not self._seeking:
            self.seek_slider.setValue(pos)
        self.time_label.setText(f"{_format_ms(pos)} / {_format_ms(self.player.duration())}")

    def _on_duration_changed(self, dur: int):
        self.seek_slider.setRange(0, max(0, dur))
        self.time_label.setText(f"{_format_ms(self.player.position())} / {_format_ms(dur)}")

    def _finish_seek(self):
        self._seeking = False
        self.player.setPosition(self.seek_slider.value())

    def stop_media(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.seek_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")

    def preview(self, *_):
        self.stop_media()
        self.video.hide()
        self.text_preview.hide()
        self.media_controls.hide()
        self.image.show()
        self.image.setPixmap(QPixmap())
        asset = self.selected_asset()
        if asset is None:
            self.image.setText("选择素材查看预览")
            self.details.clear()
            return
        path = resolve_asset_path(self.window.root, asset["path"])
        tags = asset.get("tags", [])
        tag_text = "、".join(tags) if tags else "未分类"
        extra = ""
        if asset["kind"] == "image":
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                extra = f"\n尺寸：{pixmap.width()} × {pixmap.height()} px"
                self.image.setPixmap(pixmap.scaled(
                    300, 220, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            else:
                self.image.setText("无法加载图片")
        elif asset["kind"] == "text":
            self.image.hide()
            self.text_preview.show()
            try:
                self.text_preview.setPlainText(path.read_text(encoding="utf-8")[:4000])
            except OSError as error:
                self.text_preview.setPlainText(str(error))
        elif asset["kind"] in AUDIO_KINDS:
            self.image.setPixmap(self._badge_icon(asset["kind"]).pixmap(128, 96))
            self.media_controls.show()
        elif asset["kind"] == "video":
            self.image.hide()
            self.video.show()
            self.media_controls.show()
        self.details.setText(
            f"名称：{asset['name']}\n"
            f"类型：{KIND_LABELS.get(asset['kind'], asset['kind'])}\n"
            f"分类：{tag_text}{extra}\n"
            f"路径：{asset['path']}\n"
            f"SHA-256：{asset['sha256'][:16]}…"
        )

    def play(self):
        asset = self.selected_asset()
        if asset is None or asset["kind"] not in AUDIO_KINDS | {"video"}:
            return
        path = resolve_asset_path(self.window.root, asset["path"])
        url = QUrl.fromLocalFile(str(path))
        if self.player.source() != url:
            self.player.setSource(url)
        self.player.play()
