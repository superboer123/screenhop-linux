#!/usr/bin/python3
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import dbus
import dbus.service
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402
from dbus.mainloop.glib import DBusGMainLoop  # noqa: E402

from . import __version__
from .bluez import BluezCrossing
from .config import Config
from .edge import EdgeClient
from .protocol import (
    Direction,
    Kind,
    cross_to_host,
    decode_device_state,
    decode_hosts,
    decode_layout,
    direction_between,
    enable_cross,
    layout_from_directions,
    parse,
    query_device_info,
    query_shortcut,
    set_host_info,
    set_layout,
)


class TrayIcon(dbus.service.Object):
    """Minimal StatusNotifierItem used by GNOME/KDE tray implementations."""

    IFACE = "org.kde.StatusNotifierItem"
    PROPERTIES = "org.freedesktop.DBus.Properties"

    def __init__(self, activate_callback):
        self.bus = dbus.SessionBus()
        self.activate_callback = activate_callback
        self.bus_name_text = "org.kde.StatusNotifierItem.ScreenHop"
        self.bus_name = dbus.service.BusName(self.bus_name_text, bus=self.bus)
        super().__init__(self.bus_name, "/StatusNotifierItem")
        self._register()
        GLib.timeout_add_seconds(5, self._register)

    def _values(self):
        return {
            "Category": dbus.String("ApplicationStatus"),
            "Id": dbus.String("screenhop-linux"),
            "Title": dbus.String("键鼠穿越"),
            "Status": dbus.String("Active"),
            "WindowId": dbus.UInt32(0),
            "IconName": dbus.String("screenhop"),
            "IconThemePath": dbus.String("/usr/share/icons/hicolor"),
            "ItemIsMenu": dbus.Boolean(False),
            "Menu": dbus.ObjectPath("/NO_DBUSMENU"),
        }

    def _register(self):
        try:
            watcher = self.bus.get_object(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
            )
            dbus.Interface(
                watcher,
                "org.kde.StatusNotifierWatcher",
            ).RegisterStatusNotifierItem(self.bus_name_text)
        except dbus.DBusException:
            pass
        return True

    @dbus.service.method(PROPERTIES, in_signature="ss", out_signature="v")
    def Get(self, interface, name):
        if interface != self.IFACE or name not in self._values():
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.InvalidArgs",
                "Unknown property",
            )
        return self._values()[name]

    @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self._values() if interface == self.IFACE else {}

    @dbus.service.method(PROPERTIES, in_signature="ssv", out_signature="")
    def Set(self, _interface, _name, _value):
        return

    @dbus.service.method(IFACE, in_signature="ii", out_signature="")
    def Activate(self, _x, _y):
        GLib.idle_add(self.activate_callback)

    @dbus.service.method(IFACE, in_signature="ii", out_signature="")
    def SecondaryActivate(self, _x, _y):
        GLib.idle_add(self.activate_callback)

    @dbus.service.method(IFACE, in_signature="ii", out_signature="")
    def ContextMenu(self, _x, _y):
        GLib.idle_add(self.activate_callback)

    @dbus.service.method(IFACE, in_signature="is", out_signature="")
    def Scroll(self, _delta, _orientation):
        return


class MainWindow(Adw.ApplicationWindow):
    CANVAS_WIDTH = 660
    CANVAS_HEIGHT = 370
    CARD_WIDTH = 164
    CARD_HEIGHT = 106
    CARD_POSITIONS = {
        int(Direction.TOP): (248, 18),
        int(Direction.BOTTOM): (248, 246),
        int(Direction.LEFT): (40, 132),
        int(Direction.RIGHT): (456, 132),
    }
    COMPACT_CARD_POSITIONS = {
        int(Direction.TOP): (248, 66),
        int(Direction.BOTTOM): (248, 198),
        int(Direction.LEFT): (154, 132),
        int(Direction.RIGHT): (342, 132),
    }
    LOCAL_POSITION = (248, 132)

    def __init__(self, app):
        super().__init__(
            application=app,
            title="华为键鼠穿越 Linux",
            default_width=920,
            default_height=650,
        )
        self.config = Config()
        self.bluez = BluezCrossing(self._status_from_worker, self._packet_from_worker)
        self.edge = EdgeClient(self._on_edge)
        self.hosts = []
        self.current_host = int(self.config.data.get("current_host", 1))
        self.layout = {}
        self._layout_cards = {}
        self._card_positions = {}
        self._drag_origins = {}
        self._compact_layout = False
        # Connected devices are assigned to four visual slots around the canvas.
        self._ui_directions = {}
        self._syncing_switch = False
        self._connecting = False
        self._build_ui()
        self.connect("close-request", self._hide_window)
        GLib.timeout_add_seconds(1, self._refresh_extension)
        GLib.timeout_add_seconds(3, self._connection_watch)
        if self.config.data.get("device_address"):
            GLib.timeout_add(700, self._auto_connect)

    def _auto_connect(self):
        self._connect_mouse()
        return False

    def _build_ui(self):
        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title="键鼠穿越", subtitle="多设备间自然切换键盘与鼠标")
        header.set_title_widget(title)
        self.connect_button = Gtk.Button(label="连接鼠标")
        self.connect_button.connect("clicked", self._connect_mouse)
        header.pack_end(self.connect_button)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(header)
        page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=20,
            margin_top=24,
            margin_bottom=24,
            margin_start=30,
            margin_end=30,
            hexpand=True,
        )
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(page)
        root.append(scroller)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        hero.add_css_class("card")
        icon = Gtk.Image.new_from_icon_name("input-mouse-symbolic")
        icon.set_pixel_size(52)
        hero.append(icon)
        labels = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        name = Gtk.Label(label="HUAWEI Mouse WYN", xalign=0)
        name.add_css_class("title-2")
        self.status = Gtk.Label(label="等待连接", xalign=0)
        self.status.add_css_class("dim-label")
        labels.append(name)
        labels.append(self.status)
        hero.append(labels)
        self.enable_switch = Gtk.Switch(
            valign=Gtk.Align.CENTER,
            active=bool(self.config.data["enabled"]),
        )
        self.enable_switch.connect("notify::active", self._toggle_crossing)
        hero.append(self.enable_switch)
        page.append(hero)

        layout_heading = Gtk.Box(spacing=12)
        layout_title = Gtk.Label(label="设备布局", xalign=0, hexpand=True)
        layout_title.add_css_class("title-3")
        self.layout_hint = Gtk.Label(label="连接后可拖动任意设备", xalign=1)
        self.layout_hint.add_css_class("layout-hint")
        layout_heading.append(layout_title)
        layout_heading.append(self.layout_hint)
        page.append(layout_heading)

        self.layout_box = Gtk.Fixed(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.layout_box.set_size_request(self.CANVAS_WIDTH, self.CANVAS_HEIGHT)
        self.layout_box.add_css_class("layout-canvas")
        page.append(self.layout_box)
        self._render_hosts()

        feature_group = Adw.PreferencesGroup(title="键鼠联动")
        keyboard_row = Adw.ActionRow(
            title="键盘跟随鼠标",
            subtitle="兼容的多设备键盘随鼠标一起切换主机",
        )
        self.keyboard_switch = Gtk.Switch(
            valign=Gtk.Align.CENTER,
            active=bool(self.config.data.get("keyboard_follow", False)),
        )
        self.keyboard_switch.connect("notify::active", self._toggle_keyboard_follow)
        keyboard_row.add_suffix(self.keyboard_switch)
        keyboard_row.set_activatable_widget(self.keyboard_switch)
        feature_group.add(keyboard_row)
        autostart_row = Adw.ActionRow(
            title="开机自动启动",
            subtitle="登录桌面后自动连接鼠标并保持穿越服务运行",
        )
        self.autostart_switch = Gtk.Switch(
            valign=Gtk.Align.CENTER,
            active=bool(self.config.data.get("autostart", True)),
        )
        self.autostart_switch.connect("notify::active", self._toggle_autostart)
        autostart_row.add_suffix(self.autostart_switch)
        autostart_row.set_activatable_widget(self.autostart_switch)
        feature_group.add(autostart_row)
        self.host_summary = Adw.ActionRow(
            title="连接状态",
            subtitle="当前仅显示本机",
        )
        feature_group.add(self.host_summary)
        page.append(feature_group)

        controls = Gtk.Box(spacing=10)
        refresh_btn = Gtk.Button(label="重新检测设备")
        refresh_btn.connect("clicked", self._refresh_hosts)
        controls.append(refresh_btn)
        self.extension_state = Gtk.Label(label="边缘检测：检查中", xalign=1, hexpand=True)
        controls.append(self.extension_state)
        version_label = Gtk.Label(label=f"v{__version__}", xalign=1)
        version_label.add_css_class("dim-label")
        version_label.set_tooltip_text(f"ScreenHop Linux {__version__}")
        controls.append(version_label)
        page.append(controls)
        self.log = Gtk.Label(label="", visible=False)
        self.set_content(root)

    def _device_card(self, host_index, primary):
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=5,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        box.add_css_class("device-card")
        box.add_css_class("local" if primary else "peer")
        box.set_size_request(self.CARD_WIDTH, self.CARD_HEIGHT)
        image = Gtk.Image.new_from_icon_name("video-display-symbolic")
        image.set_pixel_size(30)
        box.append(image)
        badge = Gtk.Label(label=f"蓝牙 {host_index}")
        badge.add_css_class("slot-badge")
        box.append(badge)
        kind = Gtk.Label(label="本机" if primary else "已连接")
        kind.add_css_class("device-kind")
        box.append(kind)
        gesture = Gtk.GestureDrag()
        gesture.set_button(1)
        gesture.connect("drag-begin", self._drag_begin, int(host_index))
        gesture.connect("drag-update", self._drag_update, int(host_index))
        gesture.connect("drag-end", self._drag_end, int(host_index))
        box.add_controller(gesture)
        return box

    def _render_hosts(self):
        child = self.layout_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.layout_box.remove(child)
            child = nxt
        self._layout_cards.clear()
        self._card_positions.clear()

        connected_hosts = list(self.hosts) if self.bluez.tx_path and self.hosts else []
        peers = [host for host in connected_hosts if host.index != self.current_host]
        self.layout_hint.set_text(
            "拖动任意设备调整上、下、左、右关系"
            if peers
            else "连接后按实际设备数量显示"
        )
        if not peers:
            self._compact_layout = False
            card = self._device_card(self.current_host, True)
            self._layout_cards[self.current_host] = card
            self._card_positions[self.current_host] = self.LOCAL_POSITION
            self.layout_box.put(card, *self.LOCAL_POSITION)
            return

        local = next(
            (host for host in connected_hosts if host.index == self.current_host),
            None,
        )
        all_hosts = ([local] if local else [None]) + peers
        self._compact_layout = len(all_hosts) == 2
        used = set()
        fallback = [
            int(Direction.LEFT),
            int(Direction.RIGHT),
            int(Direction.TOP),
            int(Direction.BOTTOM),
        ]
        if len(peers) == 1 and not all(
            index in self._ui_directions
            for index in (self.current_host, peers[0].index)
        ):
            relative = self._direction_for_peer(peers[0].index) or int(Direction.LEFT)
            self._ui_directions[peers[0].index] = relative
            self._ui_directions[self.current_host] = self._opposite_direction(relative)

        for host in all_hosts:
            host_index = self.current_host if host is None else host.index
            card = self._device_card(host_index, host_index == self.current_host)
            direction = self._ui_directions.get(host_index)
            if direction is None or direction in used:
                direction = next(
                    (candidate for candidate in fallback if candidate not in used),
                    int(Direction.RIGHT),
                )
            used.add(direction)
            self._ui_directions[host_index] = direction
            position = self._position_for_slot(direction)
            self._layout_cards[host_index] = card
            self._card_positions[host_index] = position
            self.layout_box.put(card, *position)

    def _drag_begin(self, gesture, _x, _y, host_index):
        self._drag_origins[host_index] = self._card_positions.get(
            host_index,
            self.LOCAL_POSITION,
        )
        gesture.get_widget().add_css_class("dragging")

    def _drag_update(self, gesture, offset_x, offset_y, host_index):
        origin = self._drag_origins.get(host_index, self.LOCAL_POSITION)
        x = max(0, min(self.CANVAS_WIDTH - self.CARD_WIDTH, origin[0] + offset_x))
        y = max(0, min(self.CANVAS_HEIGHT - self.CARD_HEIGHT, origin[1] + offset_y))
        self.layout_box.move(gesture.get_widget(), x, y)

    def _drag_end(self, gesture, offset_x, offset_y, host_index):
        widget = gesture.get_widget()
        widget.remove_css_class("dragging")
        origin = self._drag_origins.pop(
            host_index,
            self._card_positions.get(host_index, self.LOCAL_POSITION),
        )
        center_x = origin[0] + offset_x + self.CARD_WIDTH / 2
        center_y = origin[1] + offset_y + self.CARD_HEIGHT / 2
        dx = center_x - self.CANVAS_WIDTH / 2
        dy = center_y - self.CANVAS_HEIGHT / 2
        if abs(dx) >= abs(dy):
            direction = int(Direction.RIGHT if dx >= 0 else Direction.LEFT)
        else:
            direction = int(Direction.BOTTOM if dy >= 0 else Direction.TOP)

        previous = self._ui_directions.get(host_index)
        others = [index for index in self._layout_cards if index != host_index]
        if len(self._layout_cards) == 2 and others:
            other = others[0]
            other_direction = self._opposite_direction(direction)
            self._ui_directions[other] = other_direction
            other_position = self._position_for_slot(other_direction)
            self._card_positions[other] = other_position
            self.layout_box.move(self._layout_cards[other], *other_position)
        else:
            occupant = next(
                (
                    index
                    for index, item in self._ui_directions.items()
                    if index != host_index and item == direction
                ),
                None,
            )
            if occupant is not None and previous is not None:
                self._ui_directions[occupant] = previous
                other_position = self._position_for_slot(previous)
                self._card_positions[occupant] = other_position
                self.layout_box.move(self._layout_cards[occupant], *other_position)

        self._ui_directions[host_index] = direction
        position = self._position_for_slot(direction)
        self._card_positions[host_index] = position
        self.layout_box.move(widget, *position)
        self._sync_dragged_layout()

    def _position_for_slot(self, direction):
        positions = self.COMPACT_CARD_POSITIONS if self._compact_layout else self.CARD_POSITIONS
        return positions[int(direction)]

    def _sync_dragged_layout(self):
        peer_directions = {}
        local_slot = self._ui_directions.get(self.current_host)
        for host in self.hosts:
            if host.index == self.current_host or host.index not in self._ui_directions:
                continue
            peer_directions[host.index] = self._relative_direction(
                local_slot,
                self._ui_directions[host.index],
            )
        if not peer_directions:
            return
        positions = layout_from_directions(
            self.current_host,
            peer_directions,
            self.layout,
        )
        self.layout = {index: value for index, value in enumerate(positions)}
        self.config.data["layout_positions"] = positions
        self.config.data["enabled_edges"] = sorted(set(peer_directions.values()))
        self.config.save()
        self._configure_edge()
        self._set_status("正在同步设备布局…")
        if self.bluez.tx_path:
            self._run(lambda: self.bluez.write(set_layout(positions)))

    @staticmethod
    def _opposite_direction(direction):
        return {
            int(Direction.TOP): int(Direction.BOTTOM),
            int(Direction.BOTTOM): int(Direction.TOP),
            int(Direction.LEFT): int(Direction.RIGHT),
            int(Direction.RIGHT): int(Direction.LEFT),
        }[int(direction)]

    @staticmethod
    def _relative_direction(source, target):
        if source is None:
            return int(target)
        vectors = {
            int(Direction.TOP): (0, -1),
            int(Direction.BOTTOM): (0, 1),
            int(Direction.LEFT): (-1, 0),
            int(Direction.RIGHT): (1, 0),
        }
        sx, sy = vectors[int(source)]
        tx, ty = vectors[int(target)]
        dx, dy = tx - sx, ty - sy
        if abs(dx) >= abs(dy):
            return int(Direction.RIGHT if dx >= 0 else Direction.LEFT)
        return int(Direction.BOTTOM if dy >= 0 else Direction.TOP)

    def _direction_for_peer(self, host_index):
        decoded = direction_between(self.layout, self.current_host, host_index)
        if decoded is not None:
            return int(decoded)
        return {
            0x0B: int(Direction.LEFT),
            0x0C: int(Direction.RIGHT),
            0x0D: int(Direction.TOP),
            0x0E: int(Direction.BOTTOM),
        }.get(self.layout.get(host_index))

    def _run(self, func):
        def task():
            try:
                func()
            except Exception as exc:
                GLib.idle_add(self._set_status, f"错误：{exc}")

        threading.Thread(target=task, daemon=True).start()

    def _connect_mouse(self, _button=None):
        if self._connecting:
            return
        self._connecting = True
        self.connect_button.set_sensitive(False)
        address = str(self.config.data.get("device_address", ""))

        def action():
            try:
                selected = self.bluez.connect(address)
                self.config.data["device_address"] = selected["address"]
                self.config.save()
                self.bluez.write(query_device_info())
            finally:
                GLib.idle_add(self._connect_finished)

        self._run(action)

    def _connect_finished(self):
        self._connecting = False
        self.connect_button.set_sensitive(True)
        return False

    def _connection_watch(self):
        address = str(self.config.data.get("device_address", ""))
        connected = bool(address and self.bluez.is_connected(address))
        if not connected:
            if self.hosts:
                self.hosts = []
                self.layout = {}
                self._ui_directions = {}
                self._render_hosts()
                self.host_summary.set_subtitle("当前仅显示本机")
            if address and not self._connecting:
                self._connect_mouse()
        return True

    def _toggle_crossing(self, switch, _pspec):
        if self._syncing_switch:
            return
        enabled = bool(switch.get_active())
        self.config.data["enabled"] = enabled
        self.config.save()
        self._configure_edge()
        if self.bluez.tx_path:
            self._run(lambda: self.bluez.write(enable_cross(enabled)))

    def _toggle_keyboard_follow(self, switch, _pspec):
        enabled = bool(switch.get_active())
        self.config.data["keyboard_follow"] = enabled
        self.config.save()
        if enabled:
            self._set_status("键盘跟随已开启，正在检测兼容键盘…")
            if self.bluez.tx_path:
                self._run(lambda: self.bluez.write(query_shortcut()))
        else:
            self._set_status("键盘跟随已关闭")

    def _toggle_autostart(self, switch, _pspec):
        enabled = bool(switch.get_active())
        self.config.data["autostart"] = enabled
        self.config.save()
        path = Path.home() / ".config" / "autostart" / "screenhop.desktop"
        path.parent.mkdir(parents=True, exist_ok=True)
        if enabled:
            content = """[Desktop Entry]
Type=Application
Name=ScreenHop Linux
Comment=Huawei keyboard and mouse crossing
Exec=/usr/bin/screenhop-linux --background
Icon=screenhop
Terminal=false
X-GNOME-Autostart-enabled=true
"""
        else:
            # This per-user entry overrides the package's system autostart entry.
            content = """[Desktop Entry]
Type=Application
Name=ScreenHop Linux
Hidden=true
X-GNOME-Autostart-enabled=false
"""
        path.write_text(content, encoding="utf-8")
        self._set_status("已开启开机自动启动" if enabled else "已关闭开机自动启动")

    def _refresh_hosts(self, _button=None):
        if not self.bluez.tx_path:
            self._connect_mouse()
        else:
            self._set_status("正在重新检测设备…")
            self._run(lambda: self.bluez.write(query_device_info()))

    def _on_edge(self, direction, x, y):
        if not self.config.data["enabled"]:
            return
        if direction not in [int(item) for item in self.config.data["enabled_edges"]]:
            return
        target = self._target_for_edge(direction)
        if target is None:
            self.edge.rearm_after_failure()
            return

        def send_cross():
            try:
                self.bluez.write(cross_to_host(target, x, y))
            except Exception:
                self.edge.rearm_after_failure()
                raise

        self._run(send_cross)

    def _target_for_edge(self, direction):
        peers = [host for host in self.hosts if host.index != self.current_host]
        if not peers:
            return None
        if len(peers) == 1 or direction is None:
            return peers[0].index
        local_slot = self._ui_directions.get(self.current_host)
        for host in peers:
            peer_slot = self._ui_directions.get(host.index)
            if peer_slot is not None:
                peer_direction = self._relative_direction(local_slot, peer_slot)
            else:
                peer_direction = self._direction_for_peer(host.index)
            if peer_direction is not None and int(peer_direction) == int(direction):
                return host.index
        return None

    def _configure_edge(self):
        try:
            self.edge.configure(
                bool(self.config.data["enabled"]),
                int(self.config.data["edge_threshold"]),
                [int(item) for item in self.config.data["enabled_edges"]],
            )
        except Exception as exc:
            self.extension_state.set_label(f"边缘检测：{exc}")

    def _refresh_extension(self):
        ok = self.edge.available()
        self.extension_state.set_label(
            f"边缘检测：{self.edge.backend_name()}" if ok else "边缘检测：未就绪"
        )
        if ok:
            self._configure_edge()
        return False

    def _status_from_worker(self, text):
        def apply_status():
            self._set_status(text)
            if "等待自动重连" in text:
                self.hosts = []
                self.layout = {}
                self._ui_directions = {}
                self._render_hosts()
                self.host_summary.set_subtitle("连接已断开，当前仅显示本机")
            return False

        GLib.idle_add(apply_status)

    def _packet_from_worker(self, packet):
        """Run the reply-driven initialization state machine on the GTK loop."""
        try:
            parsed = parse(packet)
        except ValueError:
            GLib.idle_add(self.log.set_label, "收到辅助数据：" + packet[:24].hex(" "))
            return
        GLib.idle_add(self.log.set_label, "收到：" + packet.hex(" "))
        if parsed.opcode == 0x03 and parsed.kind == Kind.EVENT:
            state = decode_device_state(parsed)
            GLib.idle_add(self._apply_device_state, state)
            self._run(lambda: self.bluez.write(set_host_info()))
        elif parsed.opcode == 0x05 and parsed.kind == Kind.EVENT:
            hosts = decode_hosts(parsed)
            GLib.idle_add(self._apply_hosts, hosts)
            # The mouse returns both its shortcut state and current layout.
            self._run(lambda: self.bluez.write(query_shortcut()))
        elif parsed.opcode == 0x02 and parsed.kind == Kind.EVENT and len(parsed.payload) >= 2:
            GLib.idle_add(self._set_crossing_from_mouse, parsed.payload[1] == 1)
        elif parsed.opcode == 0x08:
            GLib.idle_add(self._apply_layout_state, decode_layout(parsed))
        elif parsed.opcode == 0x11:
            if self.config.data.get("keyboard_follow", False):
                GLib.idle_add(self._set_status, "键盘跟随状态已同步")
        elif parsed.opcode == 0x06 and parsed.kind == Kind.ACK and parsed.payload:
            if parsed.payload[0] != 1:
                self.edge.rearm_after_failure()
            message = (
                "穿越请求已确认"
                if parsed.payload[0] == 1
                else "穿越请求已发送，对端正在重新就绪"
            )
            GLib.idle_add(self._set_status, message)
        elif parsed.opcode == 0x06 and parsed.kind == Kind.EVENT:
            self.edge.mark_inbound()
            GLib.idle_add(self._set_status, "已从对端切入 Linux")
        elif parsed.opcode == 0x12:
            GLib.idle_add(self._set_status, "设备布局已更新")

    def _apply_device_state(self, state):
        self.current_host = state.host_index
        self.config.data["current_host"] = int(state.host_index)
        self.config.save()
        self._set_crossing_from_mouse(state.crossing_enabled)
        self._set_status("鼠标已连接")
        self._render_hosts()
        return False

    def _apply_hosts(self, hosts):
        self.hosts = list(hosts)
        count = len(hosts)
        self.host_summary.set_subtitle(
            f"已连接 {count} 台设备" if count else "当前仅显示本机"
        )
        self._set_status(f"已检测到 {count} 台设备" if count else "未检测到其他设备")
        self._render_hosts()
        return False

    def _apply_layout_state(self, layout):
        self.layout = dict(layout)
        self._ui_directions = {}
        peers = [host for host in self.hosts if host.index != self.current_host]
        edges = []
        if len(peers) == 1:
            relative = self._direction_for_peer(peers[0].index) or int(Direction.LEFT)
            self._ui_directions[peers[0].index] = relative
            self._ui_directions[self.current_host] = self._opposite_direction(relative)
            edges.append(relative)
        elif peers:
            code_to_slot = {
                0x0B: int(Direction.LEFT),
                0x0C: int(Direction.RIGHT),
                0x0D: int(Direction.TOP),
                0x0E: int(Direction.BOTTOM),
            }
            used = set()
            for host in self.hosts:
                slot = code_to_slot.get(self.layout.get(host.index))
                if slot is not None and slot not in used:
                    self._ui_directions[host.index] = slot
                    used.add(slot)
            available = [
                slot
                for slot in (
                    int(Direction.LEFT),
                    int(Direction.RIGHT),
                    int(Direction.TOP),
                    int(Direction.BOTTOM),
                )
                if slot not in used
            ]
            for host in self.hosts:
                if host.index not in self._ui_directions and available:
                    self._ui_directions[host.index] = available.pop(0)
            local_slot = self._ui_directions.get(self.current_host)
            edges.extend(
                self._relative_direction(local_slot, self._ui_directions[host.index])
                for host in peers
                if host.index in self._ui_directions
            )
        if edges:
            self.config.data["enabled_edges"] = sorted(set(edges))
            self.config.save()
            self._configure_edge()
        self._render_hosts()
        self._set_status("设备布局已同步")
        return False

    def _set_crossing_from_mouse(self, enabled):
        self._syncing_switch = True
        self.enable_switch.set_active(bool(enabled))
        self._syncing_switch = False
        self.config.data["enabled"] = bool(enabled)
        self.config.save()
        self._configure_edge()
        return False

    def _set_status(self, text):
        self.status.set_label(text)
        self.connect_button.set_sensitive(True)
        return False

    def _hide_window(self, *_args):
        self.set_visible(False)
        return True


class ScreenHopApp(Adw.Application):
    def __init__(self, background=False):
        super().__init__(
            application_id="io.github.screenhop.Linux",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.window = None
        self.tray = None
        self._suppress_first_present = bool(background)

    def _show_window(self):
        if not self.window:
            return False
        self.window.unmaximize()
        self.window.set_default_size(920, 650)
        self.window.present()
        return False

    def _toggle_window(self):
        if not self.window:
            return False
        if self.window.get_visible():
            self.window.set_visible(False)
        else:
            self._show_window()
        return False

    def do_activate(self):
        css_path = Path("/usr/lib/screenhop/style.css")
        if css_path.exists():
            from gi.repository import Gdk

            provider = Gtk.CssProvider()
            provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        if not self.window:
            self.window = MainWindow(self)
        if not self.tray:
            self.tray = TrayIcon(self._toggle_window)
        if self._suppress_first_present:
            self._suppress_first_present = False
        else:
            self._show_window()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    DBusGMainLoop(set_as_default=True)
    app = ScreenHopApp(background="--background" in sys.argv)
    return app.run(["screenhop"])


if __name__ == "__main__":
    raise SystemExit(main())
