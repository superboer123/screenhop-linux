#!/usr/bin/python3
from __future__ import annotations

import argparse
import time

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from .bluez import BluezCrossing
from .protocol import cross_to_host, enable_cross, query_device_info, set_layout


def main():
    parser = argparse.ArgumentParser(description="ScreenHop Linux diagnostics")
    parser.add_argument("command", choices=("devices", "info", "enable", "disable", "layout", "cross"))
    parser.add_argument("--address", default="")
    parser.add_argument("--direction", choices=("top", "bottom", "left", "right"), default="right")
    parser.add_argument("--target", type=int, default=2, help="target mouse host slot")
    parser.add_argument("--x", type=int, default=5); parser.add_argument("--y", type=int, default=500)
    args = parser.parse_args()
    DBusGMainLoop(set_as_default=True)
    transport = BluezCrossing(print, lambda p: print("RX", p.hex(" ")))
    if args.command == "devices":
        for item in transport.devices(): print(item)
        return 0
    transport.connect(args.address)
    if args.command == "info":
        packet = query_device_info()
    elif args.command == "enable":
        packet = enable_cross(True)
    elif args.command == "disable":
        packet = enable_cross(False)
    elif args.command == "layout":
        packet = set_layout((13, 12, 11, 14))
    else:
        packet = cross_to_host(args.target, args.x, args.y)
    transport.write(packet)
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(2, lambda: (loop.quit(), False)[1])
    loop.run()
    return 0


if __name__ == "__main__": raise SystemExit(main())

