import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_maker.maker import MakerWindow
from galgame_maker.maker_assets import AssetPickerDialog, asset_references
from galgame_maker.project import create_project, import_asset, new_id


class AssetPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_filters_and_locates_references(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "短篇")
            source = Path(folder) / "background.png"
            source.write_bytes(b"synthetic")
            asset = import_asset(root, project, source, "image", "夜景背景")
            node_id = new_id()
            project["nodes"] = [{
                "id": node_id, "name": "开场",
                "items": [{"id": new_id(), "type": "set_background", "asset_id": asset["id"]}],
            }]
            self.assertEqual(asset_references(project, asset["id"]), ["节点 开场 演出"])
            window = MakerWindow()
            window.set_project(root, project)
            window.asset_panel.query.setText("夜景")
            self.assertEqual(window.asset_panel.list.count(), 1)
            window.asset_panel.kind.setCurrentText("bgm")
            self.assertEqual(window.asset_panel.list.count(), 0)
            window.close()

    def test_batch_import_and_category_picker_filters_by_kind(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "批量导入")
            f1 = Path(folder) / "bg_room.png"
            f2 = Path(folder) / "char_hero.png"
            f3 = Path(folder) / "bgm_theme.mp3"
            f1.write_bytes(b"img1")
            f2.write_bytes(b"img2")
            f3.write_bytes(b"mp3")
            window = MakerWindow()
            window.set_project(root, project)
            with (
                patch(
                    "galgame_maker.maker_assets.QFileDialog.getOpenFileNames",
                    return_value=([str(f1), str(f2), str(f3)], ""),
                ),
                patch(
                    "galgame_maker.maker_assets.QInputDialog.getItem",
                    return_value=("自动识别", True),
                ),
                patch("galgame_maker.maker_assets.QMessageBox.information"),
            ):
                window.asset_panel.import_file()
            self.assertEqual(len(project["assets"]), 3)
            bg = next(a for a in project["assets"] if a["name"] == "bg_room")
            ch = next(a for a in project["assets"] if a["name"] == "char_hero")
            self.assertEqual(bg.get("tags"), ["背景"])
            self.assertEqual(ch.get("tags"), ["立绘"])

            # Image picker with default_tag="背景" only shows background image, never mp3
            picker = AssetPickerDialog(project, root, "image", default_tag="背景")
            self.assertEqual(picker.listing.count(), 1)
            self.assertEqual(picker.current_choice(), bg["id"])
            picker.close()
            window.dirty = False
            window.close()


if __name__ == "__main__":
    unittest.main()
