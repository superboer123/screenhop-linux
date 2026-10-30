import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE_XML = `<node><interface name="com.screenhop.Pointer">
  <method name="Configure"><arg type="b" direction="in"/><arg type="u" direction="in"/><arg type="au" direction="in"/></method>
  <signal name="EdgeReached"><arg type="u"/><arg type="u"/><arg type="u"/></signal>
</interface></node>`;

export default class ScreenHopExtension extends Extension {
    enable() {
        this._enabled = false;
        this._threshold = 2;
        this._edges = new Set([4]);
        this._lastDirection = 0;
        this._lastEmit = 0;
        this._impl = {
            Configure: (enabled, threshold, edges) => {
                this._enabled = enabled;
                this._threshold = Math.max(1, Math.min(12, threshold));
                this._edges = new Set(edges);
            },
        };
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this._impl);
        this._dbus.export(Gio.DBus.session, '/com/screenhop/Pointer');
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, 'com.screenhop.Pointer', Gio.BusNameOwnerFlags.NONE, null, null);
        this._motionId = global.stage.connect('motion-event', (_actor, event) => this._motion(event));
    }

    disable() {
        if (this._motionId) global.stage.disconnect(this._motionId);
        if (this._nameId) Gio.bus_unown_name(this._nameId);
        if (this._dbus) this._dbus.unexport();
        this._motionId = 0; this._nameId = 0; this._dbus = null;
    }

    _motion(event) {
        if (!this._enabled) return Clutter.EVENT_PROPAGATE;
        const [x, y] = event.get_coords();
        const monitor = global.display.get_monitor_geometry(global.display.get_current_monitor());
        const rx = x - monitor.x, ry = y - monitor.y;
        let direction = 0, nx = Math.round(rx * 1000 / Math.max(1, monitor.width - 1));
        let ny = Math.round(ry * 1000 / Math.max(1, monitor.height - 1));
        if (ry <= this._threshold) { direction = 1; ny = 995; }
        else if (ry >= monitor.height - 1 - this._threshold) { direction = 2; ny = 5; }
        else if (rx <= this._threshold) { direction = 3; nx = 995; }
        else if (rx >= monitor.width - 1 - this._threshold) { direction = 4; nx = 5; }
        if (!direction || !this._edges.has(direction)) { this._lastDirection = 0; return Clutter.EVENT_PROPAGATE; }
        const now = GLib.get_monotonic_time();
        if (direction !== this._lastDirection || now - this._lastEmit > 600000) {
            this._dbus.emit_signal('EdgeReached', new GLib.Variant('(uuu)', [direction, Math.max(0, Math.min(1000, nx)), Math.max(0, Math.min(1000, ny))]));
            this._lastEmit = now; this._lastDirection = direction;
        }
        return Clutter.EVENT_PROPAGATE;
    }
}

