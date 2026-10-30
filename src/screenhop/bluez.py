"""BlueZ D-Bus transport for Huawei Mouse WYN crossing GATT service.

FE40 carries ScreenHop packets. E600 carries HiLink JSON (for example
batteryInfo), while E610 is a separate device-control channel.
"""
from __future__ import annotations

import logging
from typing import Callable

import dbus

from .protocol import describe


BLUEZ = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
GATT_IFACE = "org.bluez.GattCharacteristic1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
CROSS_SERVICE = "0000fe40-cc7a-482a-984a-7f2ed5b3e512"
CROSS_RX = "0000fe42-cc7a-482a-984a-7f2ed5b3e512"
CROSS_TX = "0000fe41-cc7a-482a-984a-7f2ed5b3e512"
HILINK_SERVICE = "15f1e600-a277-43fc-a484-dd39ef8a9100"


class BluezCrossing:
    def __init__(self, on_status: Callable[[str], None] | None = None,
                 on_packet: Callable[[bytes], None] | None = None) -> None:
        self.bus = dbus.SystemBus()
        self.on_status = on_status or (lambda _s: None)
        self.on_packet = on_packet or (lambda _p: None)
        self.device_path: str | None = None
        self.rx_path: str | None = None
        self.tx_path: str | None = None
        self._signal_added = False

    def _objects(self):
        root = self.bus.get_object(BLUEZ, "/")
        return dbus.Interface(root, "org.freedesktop.DBus.ObjectManager").GetManagedObjects()

    def devices(self) -> list[dict]:
        result = []
        for path, ifaces in self._objects().items():
            props = ifaces.get(DEVICE_IFACE)
            if not props:
                continue
            uuids = [str(x).lower() for x in props.get("UUIDs", [])]
            name = str(props.get("Name", props.get("Alias", "")))
            if CROSS_SERVICE in uuids or HILINK_SERVICE in uuids or "huawei mouse" in name.lower():
                result.append({
                    "path": str(path), "name": name,
                    "address": str(props.get("Address", "")),
                    "connected": bool(props.get("Connected", False)),
                    "paired": bool(props.get("Paired", False)),
                })
        return result

    def connect(self, address: str = "") -> dict:
        devices = self.devices()
        if address:
            devices.sort(key=lambda d: d["address"].lower() != address.lower())
        if not devices:
            raise RuntimeError("未发现已配对的 HUAWEI Mouse WYN")
        selected = devices[0]
        self.device_path = selected["path"]
        dev = dbus.Interface(self.bus.get_object(BLUEZ, self.device_path), DEVICE_IFACE)
        if not selected["connected"]:
            self.on_status(f"正在连接 {selected['name']} ({selected['address']})…")
            dev.Connect(timeout=20)
        self._resolve_characteristics()
        self._start_notify()
        self.on_status(f"已连接 {selected['name']} ({selected['address']})")
        return selected

    def _resolve_characteristics(self) -> None:
        self.rx_path = self.tx_path = None
        for path, ifaces in self._objects().items():
            props = ifaces.get(GATT_IFACE)
            if not props or not str(path).startswith(str(self.device_path) + "/"):
                continue
            uuid = str(props.get("UUID", "")).lower()
            if uuid == CROSS_RX:
                self.rx_path = str(path)
            elif uuid == CROSS_TX:
                self.tx_path = str(path)
        if not self.rx_path or not self.tx_path:
            raise RuntimeError("鼠标 FE40 穿越服务尚未就绪")

    def _start_notify(self) -> None:
        if not self._signal_added:
            self.bus.add_signal_receiver(
                self._properties_changed, signal_name="PropertiesChanged",
                dbus_interface=PROPS_IFACE, path_keyword="path")
            self._signal_added = True
        rx = dbus.Interface(self.bus.get_object(BLUEZ, self.rx_path), GATT_IFACE)
        try:
            rx.StartNotify()
        except dbus.exceptions.DBusException as exc:
            if "InProgress" not in str(exc) and "Already" not in str(exc):
                raise

    def _properties_changed(self, interface, changed, _invalidated, path=None) -> None:
        if str(path) == self.device_path and str(interface) == DEVICE_IFACE and "Connected" in changed:
            if not bool(changed["Connected"]):
                self.rx_path = None
                self.tx_path = None
                self.on_status("鼠标连接已切换，正在等待自动重连…")
            return
        if str(path) != self.rx_path or str(interface) != GATT_IFACE or "Value" not in changed:
            return
        packet = bytes(int(x) for x in changed["Value"])
        logging.info("RX %s (%s)", packet.hex(" "), describe(packet))
        self.on_packet(packet)

    def is_connected(self, address: str = "") -> bool:
        for item in self.devices():
            if address and item["address"].lower() != address.lower():
                continue
            if item["connected"]:
                return True
        return False

    def write(self, packet: bytes) -> None:
        if not self.tx_path:
            raise RuntimeError("鼠标尚未连接")
        logging.info("TX %s (%s)", packet.hex(" "), describe(packet))
        char = dbus.Interface(self.bus.get_object(BLUEZ, self.tx_path), GATT_IFACE)
        value = dbus.Array([dbus.Byte(x) for x in packet], signature="y")
        # FE41 advertises a write-with-response characteristic.
        char.WriteValue(value, {"type": dbus.String("request")})

