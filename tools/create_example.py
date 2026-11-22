"""Generate the original synthetic short story used for end-to-end acceptance.

Run from the repository root with `python -m tools.create_example`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

from galgame_maker.project import create_project, save_project


NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://galgame-maker.example/short-story")


def identity(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def draw_image(path: Path, base: str, accent: str, text: str, transparent: bool = False):
    image = QImage(960, 540, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent if transparent else QColor(base))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(accent))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRect(160, 65, 640, 640))
    painter.setPen(QColor("#F6F0DE"))
    font = painter.font()
    font.setPixelSize(58)
    painter.setFont(font)
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    image.save(str(path))


def encode(filename: Path, *arguments: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments, str(filename)],
        check=True,
    )


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "examples" / "short_story"
    if root.exists():
        raise FileExistsError(f"example already exists: {root}")
    application = QGuiApplication(sys.argv)
    project = create_project(root, "晨光回声")
    project["project_id"] = identity("project")
    sources = []

    def asset(name: str, kind: str, extension: str, produce) -> str:
        asset_id = identity(f"asset:{name}")
        path = root / "assets" / f"{asset_id}{extension}"
        produce(path)
        project["assets"].append({
            "id": asset_id, "name": name, "kind": kind,
            "path": f"assets/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
        sources.append({
            "file": f"assets/{path.name}", "name": name,
            "author": "GalGame Maker project", "source": "tools/create_example.py",
            "license": "original project-generated synthetic asset",
            "attribution": "none",
        })
        return asset_id

    background = asset(
        "车站夜景", "image", ".png",
        lambda path: draw_image(path, "#102A43", "#315F73", "旧车站"),
    )
    portrait = asset(
        "林的设定图", "image", ".png",
        lambda path: draw_image(path, "#252D3B", "#7F6E72", "林"),
    )
    sprite_lin = asset(
        "林的立绘", "image", ".png",
        lambda path: draw_image(path, "#000000", "#6B8590", "林", transparent=True),
    )
    sprite_yao = asset(
        "遥的立绘", "image", ".png",
        lambda path: draw_image(path, "#000000", "#9A7457", "遥", transparent=True),
    )
    dialogue_image = asset(
        "对白框", "image", ".png",
        lambda path: draw_image(path, "#20242C", "#344659", ""),
    )
    choice_image = asset(
        "选项按钮", "image", ".png",
        lambda path: draw_image(path, "#233243", "#52677A", ""),
    )
    menu_image = asset(
        "菜单按钮", "image", ".png",
        lambda path: draw_image(path, "#26303C", "#566472", ""),
    )
    bgm = asset(
        "夜行音乐", "bgm", ".mp3",
        lambda path: encode(
            path, "-f", "lavfi", "-i", "sine=frequency=330:duration=4",
            "-c:a", "libmp3lame", "-b:a", "96k",
        ),
    )
    sfx = asset(
        "钟声", "sfx", ".wav",
        lambda path: encode(
            path, "-f", "lavfi", "-i", "sine=frequency=660:duration=0.45",
            "-c:a", "pcm_s16le",
        ),
    )
    with tempfile.TemporaryDirectory() as temporary:
        card = Path(temporary) / "credits.png"
        draw_image(card, "#152536", "#45616B", "晨光回声  ·  谢谢游玩")
        credits = asset(
            "片尾报幕", "video", ".mp4",
            lambda path: encode(
                path, "-loop", "1", "-framerate", "30", "-i", str(card),
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest",
            ),
        )

    lin = identity("character:lin")
    yao = identity("character:yao")
    project["characters"] = [
        {"id": lin, "name": "林", "description": "守候旧车站的修表匠", "reference_art_id": portrait},
        {"id": yao, "name": "遥", "description": "带着未寄出的信归来", "reference_art_id": None},
    ]
    project["relationships"] = [{
        "id": identity("relation"), "from_character_id": lin,
        "to_character_id": yao, "directed": False, "label": "旧友",
    }]
    project["documents"] = [{
        "id": identity("setting"), "kind": "setting", "title": "旧车站",
        "text": "停运的车站只有钟楼和一盏灯。每到清晨，钟声仍会响起。",
        "source_asset_id": None,
    }]
    trust = identity("variable:trust")
    project["variables"] = [{
        "id": trust, "name": "信任", "type": "int", "initial_value": 0,
    }]

    def item(name: str, kind: str, **fields):
        return {"id": identity(f"item:{name}"), "type": kind, **fields}

    start, decision, route_a, route_b, shared, dawn, farewell = [
        identity(f"node:{name}")
        for name in ("start", "decision", "route_a", "route_b", "shared", "dawn", "farewell")
    ]
    a, b = identity("choice:stay"), identity("choice:leave")
    first_line = item(
        "first-dialogue", "dialogue", character_id=lin,
        text="钟楼的灯灭了，整座城只剩下旧车站的一盏灯。",
    )
    project["nodes"] = [
        {"id": start, "name": "旧车站", "items": [
            item("background", "set_background", asset_id=background),
            item("music", "play_bgm", asset_id=bgm),
            item("bell", "play_sfx", asset_id=sfx),
            item("lin-sprite", "show_sprite", asset_id=sprite_lin, slot="left"),
            first_line,
            item("opening-narration", "narration", text="遥带着一封未寄出的信，走进夜色。"),
        ]},
        {"id": decision, "name": "去留", "items": [
            item("yao-sprite", "show_sprite", asset_id=sprite_yao, slot="right"),
            item("question", "dialogue", character_id=yao, text="天亮之前，你愿意听我说完吗？"),
        ], "choices": [
            {"id": a, "text": "留下听她说"}, {"id": b, "text": "转身离开"},
        ]},
        {"id": route_a, "name": "留下", "items": [
            item("stay-line", "dialogue", character_id=lin, text="我会等到钟声再响。"),
        ]},
        {"id": route_b, "name": "离开", "items": [
            item("leave-line", "narration", text="脚步声在空旷的站台上渐渐消失。"),
        ]},
        {"id": shared, "name": "钟声", "items": [
            item("shared-line", "narration", text="第一缕晨光穿过玻璃，钟声终于再次响起。"),
        ]},
        {"id": dawn, "name": "晨光结局", "items": [
            item("dawn-line", "dialogue", character_id=yao, text="这封信，终于可以交给你。"),
        ], "ending": {"credits_video_asset_id": credits}},
        {"id": farewell, "name": "告别结局", "items": [
            item("farewell-line", "narration", text="信留在了站台，等待下一次列车。"),
        ], "ending": {"credits_video_asset_id": None}},
    ]

    def edge(name: str, source: str, target: str, condition=None, effects=None):
        return {
            "id": identity(f"edge:{name}"),
            "from_node_id": source, "to_node_id": target,
            "condition": condition, "effects": effects or [],
        }

    project["edges"] = [
        edge("opening", start, decision),
        edge("stay", decision, route_a, {"kind": "choice", "op": "eq", "choice_id": a}, [
            {"variable_id": trust, "op": "add", "value": 1},
        ]),
        edge("leave", decision, route_b, {"kind": "choice", "op": "eq", "choice_id": b}, [
            {"variable_id": trust, "op": "subtract", "value": 1},
        ]),
        edge("stay-join", route_a, shared),
        edge("leave-join", route_b, shared),
        edge("dawn", shared, dawn, {
            "kind": "variable", "variable_id": trust, "op": "gt", "value": 0,
        }),
        edge("farewell", shared, farewell, {
            "kind": "variable", "variable_id": trust, "op": "le", "value": 0,
        }),
    ]
    project["start_node_id"] = start
    project["excerpts"] = [{
        "id": identity("excerpt"), "node_id": start,
        "item_ids": [first_line["id"]],
    }]
    project["theme"] = {
        "version": 1, "layout_id": "classic",
        "dialogue": {"image_asset_id": dialogue_image},
        "choice": {"image_asset_id": choice_image},
        "menu": {"image_asset_id": menu_image},
    }
    save_project(root, project)
    (root / "asset-sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(root)


if __name__ == "__main__":
    main()
