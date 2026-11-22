import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_maker.maker import MakerWindow
from galgame_maker.maker_assets import asset_references
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


if __name__ == "__main__":
    unittest.main()
