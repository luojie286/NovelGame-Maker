"""Maker-side import, search, preview, and reference-safe removal of media."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .project import import_asset, replace_asset, resolve_asset_path, verify_assets


EXTENSION_KIND = {
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".txt": "text", ".mp3": "bgm", ".wav": "sfx", ".mp4": "video",
}

IMAGE_CATEGORIES = ("背景", "立绘", "界面", "其他")


def infer_image_tag(name: str) -> str:
    lower = name.casefold()
    if lower.startswith(("bg", "background")) or "背景" in name:
        return "背景"
    if lower.startswith(("char", "sprite", "ch_")) or "立绘" in name or "角色" in name:
        return "立绘"
    if lower.startswith(("ui", "btn", "box", "menu")) or "界面" in name or "框" in name:
        return "界面"
    return "其他"


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
    ):
        super().__init__(parent)
        self.project = project
        self.root = root
        self.required_kind = required_kind
        self.allow_none_label = allow_none_label
        self.selected_asset_id: str | None = current_asset_id
        self.setWindowTitle(title)
        self.resize(560, 420)

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
                # Only pre-filter if at least one asset matches that tag
                has_match = any(
                    asset["kind"] == "image" and default_tag in asset.get("tags", [])
                    for asset in project["assets"]
                )
                if has_match:
                    self.category.setCurrentText(default_tag)
            self.category.currentTextChanged.connect(self._reload)
            filters.addWidget(QLabel("分类"))
            filters.addWidget(self.category)
        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称搜索素材...")
        self.search.textChanged.connect(self._reload)
        filters.addWidget(self.search, 1)
        layout.addLayout(filters)

        body = QHBoxLayout()
        self.listing = QListWidget()
        self.listing.currentItemChanged.connect(self._update_preview)
        self.listing.itemDoubleClicked.connect(lambda *_: self.accept())
        body.addWidget(self.listing, 1)

        self.preview_label = QLabel("无预览")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedSize(220, 220)
        self.preview_label.setStyleSheet("background: #1C2028; color: #9AA6B8; border-radius: 6px;")
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
        for asset in self.project["assets"]:
            if asset["kind"] != self.required_kind:
                continue
            tags = asset.get("tags", [])
            if self.required_kind == "image" and selected_cat != "全部分类":
                inferred = tags or [infer_image_tag(asset["name"])]
                if selected_cat not in inferred:
                    continue
            if needle and needle not in asset["name"].casefold():
                continue
            tag_suffix = f" [{'/'.join(tags)}]" if tags else ""
            row = QListWidgetItem(f"{asset['name']}{tag_suffix}")
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
                210, 210, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def accept(self):
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
            if item.get("asset_id") == asset_id:
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
        self.player = QMediaPlayer(self)
        audio = QAudioOutput(self.player)
        self.player.setAudioOutput(audio)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(["全部", "image", "text", "bgm", "sfx", "video"])
        self.kind.currentTextChanged.connect(self.refresh)
        controls.addWidget(self.kind)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索素材名称")
        self.query.textChanged.connect(self.refresh)
        controls.addWidget(self.query, 1)
        for caption, action in (
            ("批量导入", self.import_file),
            ("替换", self.replace_file),
            ("重命名", self.rename_asset),
            ("分类标签", self.edit_tags),
            ("移除", self.remove_asset),
            ("播放", self.play),
            ("校验完整性", self.verify_integrity),
        ):
            button = QPushButton(caption)
            button.clicked.connect(action)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.preview)
        layout.addWidget(self.list, 1)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(180)
        layout.addWidget(self.image)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(180)
        self.video.hide()
        self.player.setVideoOutput(self.video)
        layout.addWidget(self.video)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)

    def selected_asset(self) -> dict | None:
        row = self.list.currentItem()
        if row is None or self.window.project is None:
            return None
        asset_id = row.data(Qt.ItemDataRole.UserRole)
        return next(
            (asset for asset in self.window.project["assets"] if asset["id"] == asset_id), None
        )

    def refresh(self, *_):
        selected = self.selected_asset()
        selected_id = selected["id"] if selected else None
        self.list.blockSignals(True)
        self.list.clear()
        if self.window.project:
            for asset in self.window.project["assets"]:
                if self.kind.currentText() != "全部" and asset["kind"] != self.kind.currentText():
                    continue
                if self.query.text().casefold() not in asset["name"].casefold():
                    continue
                tags = asset.get("tags", [])
                tag_suffix = f" [{'/'.join(tags)}]" if tags else ""
                row = QListWidgetItem(f"{asset['name']} · {asset['kind']}{tag_suffix}")
                row.setData(Qt.ItemDataRole.UserRole, asset["id"])
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
            kinds = ["image", "text", "bgm", "sfx", "video"]
            selected, accepted = QInputDialog.getItem(
                self, "素材用途", "类型", kinds, kinds.index(suggested), editable=False,
            )
            if not accepted:
                return
            name, accepted = QInputDialog.getText(self, "素材名称", "名称", text=source.stem)
            if not accepted or not name.strip():
                return
            tags: list[str] = []
            if selected == "image":
                default_tag = infer_image_tag(name.strip())
                tag_choice, accepted = QInputDialog.getItem(
                    self, "图片分类", "分类（用于剧情编辑筛选）",
                    list(IMAGE_CATEGORIES), list(IMAGE_CATEGORIES).index(default_tag),
                    editable=True,
                )
                if accepted and tag_choice.strip():
                    tags = [tag_choice.strip()]
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
                choice, accepted = QInputDialog.getItem(
                    self, "批量导入图片分类", "图片分类标签",
                    ["自动识别", *IMAGE_CATEGORIES], 0, editable=True,
                )
                if not accepted:
                    return
                batch_tag = choice.strip() or "自动识别"
            imported: list[dict] = []
            skipped: list[str] = []
            for filename in filenames:
                source = Path(filename)
                kind = EXTENSION_KIND.get(source.suffix.lower())
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

    def edit_tags(self):
        asset = self.selected_asset()
        if asset is None:
            return
        current = ", ".join(asset.get("tags", []))
        raw, accepted = QInputDialog.getText(
            self, "编辑分类标签", "标签（多个用逗号分隔，例如：背景、立绘、界面）", text=current,
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
        if suggested != asset["kind"]:
            QMessageBox.warning(
                self, "类型不匹配",
                f"当前素材类型为 {asset['kind']}，所选文件类型为 {suggested}",
            )
            return
        try:
            self.player.stop()
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
        self.player.stop()
        self.window.project["assets"].remove(asset)
        self.window.mark_dirty()
        self.refresh()
        self.window.character_panel.refresh()
        self.window.theme_panel.load_part()

    def preview(self, *_):
        self.player.stop()
        self.video.hide()
        self.image.clear()
        asset = self.selected_asset()
        if asset is None:
            self.details.clear()
            return
        path = resolve_asset_path(self.window.root, asset["path"])
        self.details.setText(f"{asset['name']}\n{asset['path']}\nSHA-256 {asset['sha256']}")
        if asset["kind"] == "image":
            pixmap = QPixmap(str(path))
            self.image.setPixmap(pixmap.scaled(
                640, 240, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        elif asset["kind"] == "text":
            try:
                self.details.setText(path.read_text(encoding="utf-8")[:1000])
            except OSError as error:
                self.details.setText(str(error))
        elif asset["kind"] == "video":
            self.video.show()

    def play(self):
        asset = self.selected_asset()
        if asset is None or asset["kind"] not in {"bgm", "sfx", "video"}:
            return
        path = resolve_asset_path(self.window.root, asset["path"])
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
