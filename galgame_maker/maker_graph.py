"""Interactive pannable/zoomable canvases and live-updating node/edge items."""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
)


class InteractiveGraphView(QGraphicsView):
    """Pannable and zoomable graph canvas shared by story and relationship views."""

    def __init__(self, scene: QGraphicsScene, dark: bool = True, parent=None):
        super().__init__(scene, parent)
        self.dark = dark
        self._panning = False
        self._pan_start = QPointF()
        self._zoom = 1.0
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        if self.dark:
            painter.fillRect(rect, QColor("#181B22"))
            grid = 28
            left = int(math.floor(rect.left() / grid)) * grid
            top = int(math.floor(rect.top() / grid)) * grid
            painter.setPen(QPen(QColor("#232833"), 1))
            x = left
            while x <= rect.right():
                painter.drawLine(x, rect.top(), x, rect.bottom())
                x += grid
            y = top
            while y <= rect.bottom():
                painter.drawLine(rect.left(), y, rect.right(), y)
                y += grid
        else:
            painter.fillRect(rect, QColor("#F6F8FB"))
            grid = 28
            left = int(math.floor(rect.left() / grid)) * grid
            top = int(math.floor(rect.top() / grid)) * grid
            painter.setPen(QPen(QColor("#CFD7E3"), 2))
            x = left
            while x <= rect.right():
                y = top
                while y <= rect.bottom():
                    painter.drawPoint(int(x), int(y))
                    y += grid
                x += grid

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.12 if delta > 0 else 1 / 1.12
        new_zoom = self._zoom * factor
        if 0.35 <= new_zoom <= 2.4:
            self._zoom = new_zoom
            self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and self.itemAt(event.position().toPoint()) is None
        ):
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(
                int(self.horizontalScrollBar().value() - delta.x())
            )
            self.verticalScrollBar().setValue(
                int(self.verticalScrollBar().value() - delta.y())
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and event.button() in {
            Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton,
        }:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


def cubic_wire_path(start: QPointF, end: QPointF) -> QPainterPath:
    """Create a smooth ComfyUI-style horizontal cubic Bezier curve."""
    path = QPainterPath(start)
    span = max(60.0, abs(end.x() - start.x()) * 0.5)
    c1 = QPointF(start.x() + span, start.y())
    c2 = QPointF(end.x() - span, end.y())
    path.cubicTo(c1, c2, end)
    return path


class StoryEdgeItem(QGraphicsPathItem):
    """Live-updating Bezier connection between two story node cards."""

    def __init__(
        self,
        source: "StoryNodeItem",
        target: "StoryNodeItem",
        label: str = "",
        conflict: bool = False,
    ):
        super().__init__()
        self.source = source
        self.target = target
        self.label = label
        self.conflict = conflict
        self.setZValue(-1)
        color = QColor("#E05A47") if conflict else QColor("#67B3E8")
        self.edge_color = color
        self.setPen(QPen(color, 2.4))
        self.source.edges.append(self)
        self.target.edges.append(self)
        self.update_path()

    def update_path(self) -> None:
        start = self.source.output_port_scene_pos()
        end = self.target.input_port_scene_pos()
        self.prepareGeometryChange()
        self.setPath(cubic_wire_path(start, end))

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self.pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = self.path()
        painter.drawPath(path)

        # Arrowhead at target port
        end = self.target.input_port_scene_pos()
        arrow = QPolygonF([
            end,
            QPointF(end.x() - 10, end.y() - 5),
            QPointF(end.x() - 10, end.y() + 5),
        ])
        painter.setBrush(self.edge_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(arrow)

        # Condition label pill at curve midpoint
        if self.label:
            mid = path.pointAtPercent(0.5)
            font = QFont()
            font.setPixelSize(11)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            text = self.label if len(self.label) <= 22 else self.label[:20] + "…"
            tw = metrics.horizontalAdvance(text) + 12
            th = metrics.height() + 4
            pill = QRectF(mid.x() - tw / 2, mid.y() - th / 2, tw, th)
            painter.setBrush(QColor("#222732"))
            painter.setPen(QPen(self.edge_color, 1))
            painter.drawRoundedRect(pill, 6, 6)
            painter.setPen(QColor("#E6EDF7"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


class StoryNodeItem(QGraphicsItem):
    """ComfyUI-inspired story node card with painted title, ports, and drag-connect."""

    WIDTH = 224
    HEIGHT = 96
    HEADER_H = 32
    PORT_RADIUS = 7

    def __init__(
        self,
        window,
        node: dict,
        x: float,
        y: float,
        status: str = "pass",
        is_start: bool = False,
        is_ending: bool = False,
        is_unended_leaf: bool = False,
    ):
        super().__init__()
        self.window = window
        self.node = node
        self.status = status
        self.is_start = is_start
        self.is_ending = is_ending
        self.is_unended_leaf = is_unended_leaf
        self.edges: list[StoryEdgeItem] = []
        self._dragging_wire = False
        self._temp_wire: QGraphicsPathItem | None = None
        self._moved = False

        self.setPos(x, y)
        self.setZValue(1)
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def boundingRect(self) -> QRectF:
        pad = self.PORT_RADIUS + 4
        return QRectF(-pad, -4, self.WIDTH + pad * 2, self.HEIGHT + 8)

    def input_port_local(self) -> QPointF:
        return QPointF(0, self.HEIGHT / 2)

    def output_port_local(self) -> QPointF:
        return QPointF(self.WIDTH, self.HEIGHT / 2)

    def input_port_scene_pos(self) -> QPointF:
        return self.mapToScene(self.input_port_local())

    def output_port_scene_pos(self) -> QPointF:
        return self.mapToScene(self.output_port_local())

    def _near_output_port(self, local_pos: QPointF) -> bool:
        out = self.output_port_local()
        return math.hypot(local_pos.x() - out.x(), local_pos.y() - out.y()) <= 18

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body_rect = QRectF(0, 0, self.WIDTH, self.HEIGHT)

        # Colors based on node role & branch check state
        header_color = QColor("#303846")
        border_color = QColor("#546275")
        body_color = QColor("#232832")
        badges: list[tuple[str, str]] = []
        if self.is_start:
            header_color = QColor("#1F4B6E")
            border_color = QColor("#4EA8DE")
            badges.append(("开场", "#4EA8DE"))
        if self.is_ending:
            header_color = QColor("#1E4D3B")
            border_color = QColor("#52B788")
            badges.append(("结局", "#52B788"))
        elif self.is_unended_leaf:
            border_color = QColor("#D4A373")
            badges.append(("终点·未设结局", "#D4A373"))
        if self.status == "conflict":
            header_color = QColor("#692924")
            border_color = QColor("#E05A47")
            body_color = QColor("#362020")
            badges.append(("分支冲突", "#E05A47"))
        elif self.status == "incomplete":
            border_color = QColor("#E0A447")
            badges.append(("未完成", "#E0A447"))
        if self.isSelected():
            border_color = QColor("#8BD3FF")

        # Card body
        card_path = QPainterPath()
        card_path.addRoundedRect(body_rect, 10, 10)
        painter.fillPath(card_path, body_color)

        # Header bar
        painter.save()
        painter.setClipPath(card_path)
        painter.fillRect(QRectF(0, 0, self.WIDTH, self.HEADER_H), header_color)
        painter.restore()

        # Border
        painter.setPen(QPen(border_color, 2.4 if self.isSelected() else 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(card_path)

        # Node title in header (guaranteed crisp & visible)
        title_font = QFont()
        title_font.setPixelSize(14)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#F4F7FB"))
        title_rect = QRectF(12, 0, self.WIDTH - 24, self.HEADER_H)
        title_text = painter.fontMetrics().elidedText(
            self.node["name"], Qt.TextElideMode.ElideRight, int(title_rect.width()),
        )
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title_text)

        # Summary line in body
        info_font = QFont()
        info_font.setPixelSize(11)
        painter.setFont(info_font)
        painter.setPen(QColor("#A9B5C9"))
        items_count = len(self.node.get("items", []))
        choices_count = len(self.node.get("choices", []))
        summary = f"演出 {items_count} 项"
        if choices_count:
            summary += f" · 选项 {choices_count} 个"
        painter.drawText(
            QRectF(12, self.HEADER_H + 6, self.WIDTH - 24, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            summary,
        )

        # Badges row
        bx = 12.0
        by = self.HEADER_H + 32.0
        badge_font = QFont()
        badge_font.setPixelSize(10)
        painter.setFont(badge_font)
        for text, hex_color in badges[:3]:
            bw = painter.fontMetrics().horizontalAdvance(text) + 12
            brect = QRectF(bx, by, bw, 18)
            painter.setBrush(QColor(hex_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(brect, 4, 4)
            painter.setPen(QColor("#11161F"))
            painter.drawText(brect, Qt.AlignmentFlag.AlignCenter, text)
            bx += bw + 6

        # Input port (left / head) & Output port (right / tail)
        painter.setPen(QPen(QColor("#181B22"), 2))
        painter.setBrush(QColor("#5EC2F2"))
        painter.drawEllipse(self.input_port_local(), self.PORT_RADIUS, self.PORT_RADIUS)
        if not self.is_ending:
            painter.setBrush(QColor("#F4B942"))
            painter.drawEllipse(self.output_port_local(), self.PORT_RADIUS, self.PORT_RADIUS)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._moved = True
            for edge in self.edges:
                edge.update_path()
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.window.select_node(self.node["id"])
            if not self.is_ending and self._near_output_port(event.pos()):
                self._dragging_wire = True
                wire = QGraphicsPathItem()
                wire.setPen(QPen(QColor("#F4B942"), 2.5, Qt.PenStyle.DashLine))
                wire.setZValue(10)
                wire.setPath(cubic_wire_path(self.output_port_scene_pos(), event.scenePos()))
                self.scene().addItem(wire)
                self._temp_wire = wire
                event.accept()
                return
            self._moved = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging_wire and self._temp_wire is not None:
            self._temp_wire.setPath(
                cubic_wire_path(self.output_port_scene_pos(), event.scenePos())
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging_wire:
            self._dragging_wire = False
            if self._temp_wire is not None:
                self.scene().removeItem(self._temp_wire)
                self._temp_wire = None
            # Find target StoryNodeItem under release point
            target_node = None
            for item in self.scene().items(event.scenePos()):
                if isinstance(item, StoryNodeItem) and item is not self:
                    target_node = item.node
                    break
            event.accept()
            if target_node is not None:
                self.window.quick_connect_nodes(self.node["id"], target_node["id"])
            return
        super().mouseReleaseEvent(event)
        if self._moved:
            positions = self.window.project.setdefault("editor", {}).setdefault("node_positions", {})
            positions[self.node["id"]] = {"x": round(self.x()), "y": round(self.y())}
            self.window.mark_dirty()
            self._moved = False
            rect = self.scene().itemsBoundingRect().adjusted(-180, -180, 180, 180)
            self.scene().setSceneRect(self.scene().sceneRect().united(rect))


class RelationEdgeItem(QGraphicsPathItem):
    """Live-updating relationship edge between two draggable character nodes."""

    def __init__(
        self,
        source: "CharacterNodeItem",
        target: "CharacterNodeItem",
        label: str,
        directed: bool = True,
        curve_offset: float = 0.0,
    ):
        super().__init__()
        self.source = source
        self.target = target
        self.label = label
        self.directed = directed
        self.curve_offset = curve_offset
        self.setZValue(-1)
        self.edge_color = QColor("#C84B6E") if directed else QColor("#4A7FB5")
        self.setPen(QPen(self.edge_color, 2.0))
        self.source.edges.append(self)
        self.target.edges.append(self)
        self.update_path()

    def update_path(self) -> None:
        p1 = self.source.scenePos()
        p2 = self.target.scenePos()
        path = QPainterPath(p1)
        mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist > 0 and self.curve_offset:
            nx, ny = -dy / dist, dx / dist
            ctrl = QPointF(mid.x() + nx * self.curve_offset, mid.y() + ny * self.curve_offset)
            path.quadTo(ctrl, p2)
        else:
            path.lineTo(p2)
        self.prepareGeometryChange()
        self.setPath(path)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = self.path()
        painter.setPen(self.pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        p1 = self.source.scenePos()
        p2 = self.target.scenePos()
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if self.directed and dist > CharacterNodeItem.RADIUS + 8:
            ux, uy = dx / dist, dy / dist
            tip = QPointF(p2.x() - ux * (CharacterNodeItem.RADIUS + 2), p2.y() - uy * (CharacterNodeItem.RADIUS + 2))
            base = QPointF(tip.x() - ux * 12, tip.y() - uy * 12)
            arrow = QPolygonF([
                tip,
                QPointF(base.x() - uy * 6, base.y() + ux * 6),
                QPointF(base.x() + uy * 6, base.y() - ux * 6),
            ])
            painter.setBrush(self.edge_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(arrow)

        if self.label:
            mid = path.pointAtPercent(0.5)
            font = QFont()
            font.setPixelSize(11)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(self.label) + 12
            th = metrics.height() + 4
            pill = QRectF(mid.x() - tw / 2, mid.y() - th / 2, tw, th)
            painter.setBrush(QColor(255, 255, 255, 235))
            painter.setPen(QPen(QColor("#D0D7E2"), 1))
            painter.drawRoundedRect(pill, 6, 6)
            painter.setPen(QColor("#2D3748"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, self.label)


class CharacterNodeItem(QGraphicsItem):
    """Draggable colored circle node for the character relationship graph."""

    RADIUS = 30
    PALETTE = ("#2B6CB0", "#C53030", "#2F855A", "#805AD5", "#DD6B20", "#319795")

    def __init__(
        self,
        character: dict,
        color_index: int,
        x: float,
        y: float,
        on_moved: Callable[[str, int, int], None],
        on_selected: Callable[[str], None],
    ):
        super().__init__()
        self.character = character
        self.color = QColor(self.PALETTE[color_index % len(self.PALETTE)])
        self.on_moved = on_moved
        self.on_selected = on_selected
        self.edges: list[RelationEdgeItem] = []
        self._moved = False
        self.setPos(x, y)
        self.setZValue(1)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def boundingRect(self) -> QRectF:
        return QRectF(-self.RADIUS - 40, -self.RADIUS - 6, (self.RADIUS + 40) * 2, self.RADIUS * 2 + 34)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#1A202C") if self.isSelected() else QColor("#FFFFFF"), 3))
        painter.setBrush(self.color)
        painter.drawEllipse(QPointF(0, 0), self.RADIUS, self.RADIUS)

        # Initial glyph inside circle
        initial_font = QFont()
        initial_font.setPixelSize(16)
        initial_font.setBold(True)
        painter.setFont(initial_font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(
            QRectF(-self.RADIUS, -self.RADIUS, self.RADIUS * 2, self.RADIUS * 2),
            Qt.AlignmentFlag.AlignCenter,
            self.character["name"][:2],
        )

        # Full name pill below circle
        name_font = QFont()
        name_font.setPixelSize(12)
        name_font.setBold(True)
        painter.setFont(name_font)
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(self.character["name"]) + 10
        rect = QRectF(-tw / 2, self.RADIUS + 4, tw, metrics.height() + 2)
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor("#1A202C"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.character["name"])

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._moved = True
            for edge in self.edges:
                edge.update_path()
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._moved = False
            self.on_selected(self.character["id"])
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self._moved:
            self.on_moved(self.character["id"], round(self.x()), round(self.y()))
            self._moved = False
