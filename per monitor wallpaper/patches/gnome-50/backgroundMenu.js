import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as BoxPointer from './boxpointer.js';
import * as PopupMenu from './popupMenu.js';
import * as Util from '../misc/util.js';

import * as Main from './main.js';

const PER_MONITOR_HELPER = 'gnome-per-monitor-background';

/**
 * Resolve the per-monitor helper binary.
 * Prefer ~/.local/bin (installer default), then PATH.
 *
 * @returns {string|null}
 */
function _findPerMonitorHelper() {
    const homePath = GLib.build_filenamev([
        GLib.get_home_dir(), '.local', 'bin', PER_MONITOR_HELPER,
    ]);
    const homeFile = Gio.File.new_for_path(homePath);

    try {
        if (homeFile.query_exists(null)) {
            const info = homeFile.query_info(
                Gio.FILE_ATTRIBUTE_ACCESS_CAN_EXECUTE,
                Gio.FileQueryInfoFlags.NONE,
                null);
            if (info.get_attribute_boolean(Gio.FILE_ATTRIBUTE_ACCESS_CAN_EXECUTE))
                return homePath;
            console.warn(`Per-monitor helper exists but is not executable: ${homePath}`);
        }
    } catch (e) {
        console.warn(`Failed to inspect per-monitor helper at ${homePath}: ${e.message}`);
    }

    return GLib.find_program_in_path(PER_MONITOR_HELPER);
}

function _launchPerMonitorHelper() {
    const path = _findPerMonitorHelper();
    if (!path) {
        Main.notifyError(
            _('Unable to open Per-Monitor Backgrounds'),
            _('Install the helper tool first by re-running the per-monitor wallpaper installer.'));
        return;
    }

    try {
        Util.trySpawn([path]);
    } catch (e) {
        logError(e, 'Failed to launch per-monitor background tool');
        Main.notifyError(
            _('Unable to open Per-Monitor Backgrounds'),
            _('Install the helper tool first by re-running the per-monitor wallpaper installer.'));
    }
}

export class BackgroundMenu extends PopupMenu.PopupMenu {
    constructor(layoutManager) {
        super(layoutManager.dummyCursor, 0, St.Side.TOP);

        this.addSettingsAction(_('Change Background…'), 'gnome-background-panel.desktop');
        this.addAction(_('Per-Monitor Backgrounds…'), () => _launchPerMonitorHelper());
        this.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.addSettingsAction(_('Display Settings'), 'gnome-display-panel.desktop');
        this.addSettingsAction(_('Settings'), 'org.gnome.Settings.desktop');

        this.actor.add_style_class_name('background-menu');

        layoutManager.uiGroup.add_child(this.actor);
        this.actor.hide();
    }
}

/**
 * @param {Meta.BackgroundActor} actor
 * @param {import('./layout.js').LayoutManager} layoutManager
 */
export function addBackgroundMenu(actor, layoutManager) {
    actor.reactive = true;
    actor._backgroundMenu = new BackgroundMenu(layoutManager);
    actor._backgroundManager = new PopupMenu.PopupMenuManager(actor);
    actor._backgroundManager.addMenu(actor._backgroundMenu);

    function openMenu(x, y) {
        Main.layoutManager.setDummyCursorGeometry(x, y, 0, 0);
        actor._backgroundMenu.open(BoxPointer.PopupAnimation.FULL);
    }

    const longPressGesture = new Clutter.LongPressGesture({
        required_button: Clutter.BUTTON_PRIMARY,
    });
    longPressGesture.connect('recognize', () => {
        if (actor._backgroundMenu.isOpen)
            return;

        const {x, y} = longPressGesture.get_coords_abs();
        openMenu(x, y);
    });
    actor.add_action(longPressGesture);

    const clickGesture = new Clutter.ClickGesture({
        required_button: Clutter.BUTTON_SECONDARY,
        recognize_on_press: true,
    });
    clickGesture.connect('recognize', () => {
        const {x, y} = clickGesture.get_coords_abs();
        openMenu(x, y);
    });
    actor.add_action(clickGesture);

    actor.connect('destroy', () => {
        actor._backgroundMenu.destroy();
        actor._backgroundMenu = null;
        actor._backgroundManager = null;
    });
}
