"""Maker UI panel for built-in project version history, ID diffing, and verified restore."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .versioning import (
    VersionError,
    VersionIntegrityError,
    VersionRecord,
    create_version,
    delete_version,
    diff_versions,
    diff_with_current,
    list_versions,
    prune_unused_assets,
    restore_version,
    verify_version,
)


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


class VersionPanel(QWidget):
    """Manage project snapshots, compare by stable ID, verify integrity, and restore."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._records: list[VersionRecord] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        toolbar = QHBoxLayout()
        for caption, handler, accent in (
            ("创建命名快照", self.create_snapshot, True),
            ("与当前项目对比", self.compare_with_current, False),
            ("校验完整性", self.verify_selected, False),
            ("恢复此版本", self.restore_selected, False),
            ("删除版本", self.delete_selected, False),
            ("清理历史素材", self.cleanup_unused_assets, False),
        ):
            button = QPushButton(caption)
            if accent:
                button.setProperty("accent", True)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch()
        outer.addLayout(toolbar)

        compare_row = QHBoxLayout()
        compare_row.addWidget(QLabel("双版本对比：旧版本"))
        self.old_combo = QComboBox()
        compare_row.addWidget(self.old_combo, 1)
        compare_row.addWidget(QLabel("新版本"))
        self.new_combo = QComboBox()
        compare_row.addWidget(self.new_combo, 1)
        compare_btn = QPushButton("对比差异")
        compare_btn.clicked.connect(self.compare_pair)
        compare_row.addWidget(compare_btn)
        outer.addLayout(compare_row)

        split = QSplitter()
        outer.addWidget(split, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("历史版本快照（按时间倒序）")
        title.setProperty("sectionTitle", True)
        left_layout.addWidget(title)
        self.versions_list = QListWidget()
        self.versions_list.currentItemChanged.connect(self._on_version_selected)
        left_layout.addWidget(self.versions_list, 1)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)
        diff_title = QLabel("版本详情与对象 ID 差异")
        diff_title.setProperty("sectionTitle", True)
        right_layout.addWidget(diff_title)
        self.detail_view = QTextEdit()
        self.detail_view.setReadOnly(True)
        right_layout.addWidget(self.detail_view, 1)
        split.addWidget(right)
        split.setSizes([520, 680])

    def _selected_record(self) -> VersionRecord | None:
        item = self.versions_list.currentItem()
        if item is None:
            return None
        version_id = item.data(Qt.ItemDataRole.UserRole)
        return next((r for r in self._records if r.version_id == version_id), None)

    def _record_caption(self, record: VersionRecord) -> str:
        tag = "自动" if record.auto else "手动"
        nodes = record.stats.get("nodes", 0)
        assets = record.stats.get("assets", 0)
        return (
            f"[{tag}] {record.label} · {record.created_at} · "
            f"{nodes} 节点 / {assets} 素材 [{record.version_id[:8]}]"
        )

    def refresh(self):
        self.versions_list.clear()
        self.old_combo.clear()
        self.new_combo.clear()
        self._records = []
        if self.window.root is None or self.window.project is None:
            self.detail_view.setPlainText("请先新建或打开项目。")
            return
        try:
            self._records = list_versions(self.window.root)
        except VersionError as error:
            self.detail_view.setPlainText(f"读取版本历史失败：{error}")
            return

        for record in self._records:
            caption = self._record_caption(record)
            row = QListWidgetItem(caption)
            row.setData(Qt.ItemDataRole.UserRole, record.version_id)
            self.versions_list.addItem(row)
            self.old_combo.addItem(caption, record.version_id)
            self.new_combo.addItem(caption, record.version_id)

        if len(self._records) >= 2:
            self.old_combo.setCurrentIndex(1)
            self.new_combo.setCurrentIndex(0)

        if self.versions_list.count():
            self.versions_list.setCurrentRow(0)
        else:
            self.detail_view.setPlainText(
                "当前尚无历史版本快照。\n保存项目时会自动记录快照，也可点击「创建命名快照」手动保存。"
            )

    def _on_version_selected(self, current, previous):
        record = self._selected_record()
        if record is None:
            return
        lines = [
            f"版本 ID：{record.version_id}",
            f"标签：{record.label}（{'自动保存' if record.auto else '手动快照'}）",
            f"创建时间：{record.created_at}",
            f"快照摘要：{record.project_sha256[:16]}...",
            f"引用素材数（按内容哈希去重）：{len(record.asset_hashes)}",
            f"统计：节点 {record.stats.get('nodes', 0)} · 连接 {record.stats.get('edges', 0)} · "
            f"素材 {record.stats.get('assets', 0)} · 角色 {record.stats.get('characters', 0)} · "
            f"设定 {record.stats.get('documents', 0)} · 摘录 {record.stats.get('excerpts', 0)}",
        ]
        self.detail_view.setPlainText("\n".join(lines))

    def create_snapshot(self):
        if not self.window.require_project():
            return
        label, accepted = QInputDialog.getText(
            self, "创建命名快照", "版本说明", text="里程碑快照"
        )
        if not accepted:
            return
        try:
            record = create_version(
                self.window.root,
                self.window.project,
                label=label.strip() or "手动快照",
                auto=False,
                skip_if_unchanged=False,
            )
        except (OSError, ValueError, VersionError) as error:
            QMessageBox.warning(self, "创建快照失败", str(error))
            return
        self.refresh()
        self.detail_view.setPlainText(
            f"已创建快照：{record.label} [{record.version_id[:8]}]\n"
            f"创建时间：{record.created_at}"
        )

    def compare_with_current(self):
        if not self.window.require_project():
            return
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "对比差异", "请先选择一个历史版本")
            return
        try:
            diff = diff_with_current(
                self.window.root, record.version_id, self.window.project
            )
        except VersionError as error:
            QMessageBox.warning(self, "对比失败", str(error))
            return
        header = f"对比：{record.label} [{record.version_id[:8]}] -> 当前编辑状态\n" + "-" * 48
        self.detail_view.setPlainText(header + "\n" + "\n".join(diff.summary_lines()))

    def compare_pair(self):
        if not self.window.require_project():
            return
        old_id = self.old_combo.currentData()
        new_id = self.new_combo.currentData()
        if not old_id or not new_id:
            QMessageBox.information(self, "对比差异", "需要至少一个版本才能对比")
            return
        try:
            diff = diff_versions(self.window.root, old_id, new_id)
        except VersionError as error:
            QMessageBox.warning(self, "对比失败", str(error))
            return
        header = f"对比：[{old_id[:8]}] -> [{new_id[:8]}]\n" + "-" * 48
        self.detail_view.setPlainText(header + "\n" + "\n".join(diff.summary_lines()))

    def verify_selected(self):
        if not self.window.require_project():
            return
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "校验完整性", "请先选择一个历史版本")
            return
        issues = verify_version(self.window.root, record.version_id)
        if not issues:
            self.detail_view.setPlainText(
                f"完整性校验通过：{record.label} [{record.version_id[:8]}]\n"
                f"快照 JSON 格式有效，引用的 {len(record.asset_hashes)} 个素材哈希全部一致。"
            )
            return
        lines = [f"完整性校验发现 {len(issues)} 个问题："]
        for issue in issues:
            lines.append(f"- [{issue.scope}] {issue.detail}")
        self.detail_view.setPlainText("\n".join(lines))

    def restore_selected(self):
        if not self.window.require_project():
            return
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "恢复版本", "请先选择一个历史版本")
            return
        confirm = QMessageBox.question(
            self,
            "确认恢复版本",
            f"确定恢复到「{record.label}」({record.created_at}) 吗？\n"
            "恢复前将先校验快照格式与素材完整性，并自动备份当前已保存版本。",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            restored = restore_version(
                self.window.root, record.version_id, backup_current=True
            )
        except VersionIntegrityError as error:
            details = "\n".join(f"- {issue.detail}" for issue in error.issues)
            QMessageBox.warning(
                self, "恢复被阻止（完整性校验未通过）", f"{error}\n\n{details}"
            )
            return
        except (OSError, ValueError, VersionError) as error:
            QMessageBox.warning(self, "恢复失败", str(error))
            return
        self.window.set_project(self.window.root, restored)
        QMessageBox.information(
            self, "恢复成功", f"已恢复到版本「{record.label}」[{record.version_id[:8]}]。"
        )

    def delete_selected(self):
        if not self.window.require_project():
            return
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "删除版本", "请先选择一个历史版本")
            return
        confirm = QMessageBox.question(
            self,
            "删除历史版本",
            f"确定删除版本「{record.label}」[{record.version_id[:8]}] 吗？\n"
            "仅被此版本引用的历史素材副本也将被自动清理。",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            report = delete_version(self.window.root, record.version_id, prune=True)
        except VersionError as error:
            QMessageBox.warning(self, "删除失败", str(error))
            return
        self.refresh()
        self.detail_view.setPlainText(
            f"已删除版本 [{record.version_id[:8]}]。\n"
            f"清理无引用历史素材对象：{report.removed_objects} 个，释放 {_format_bytes(report.freed_bytes)}。"
        )

    def cleanup_unused_assets(self):
        if not self.window.require_project():
            return
        report = prune_unused_assets(
            self.window.root,
            current_project=self.window.project,
            clean_orphan_assets=True,
        )
        self.detail_view.setPlainText(
            "历史素材与孤立文件清理完成：\n"
            f"- 清理无引用历史对象：{report.removed_objects} 个\n"
            f"- 清理未引用素材文件：{report.removed_orphan_assets} 个\n"
            f"- 合计释放空间：{_format_bytes(report.freed_bytes)}"
        )
