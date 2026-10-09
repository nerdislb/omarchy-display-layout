# Display Layout for Omarchy

A native bar plugin for arranging your displays visually. Built for Hyprland's
Lua configuration and Omarchy's existing Quickshell bar — no replacement bar,
root access, new toolkit or background startup service.

## Use

In the existing **System → Display & brightness** popup, select **Layout**.
The **Display & brightness** tab keeps the original brightness, text-size,
scale and display controls. It is disabled during a pending preview.

A standalone stacked-display bar button is also available for other setups.
The rectangles show
**logical screen space**, not the physical size of the monitors.

1. Select a display and drag it into place. Edges and centres snap.
2. Or choose **Above laptop**, **Left**, **Right** or **Below** relative to the
   other display. With more displays, select the anchor in **Relative to**.
3. **Apply** starts a 20-second live preview. Test the pointer crossing.
4. **Keep** saves the layout. **Revert**, closing the popup, or leaving the
   preview unconfirmed restores the previous positions.

For a laptop at `(0, 0)` and a same-sized monitor above it, the external monitor
is at `(0, -1080)`. The laptop's origin is not moved. No display is disabled;
resolution, refresh rate, scale, rotation and existing monitor options are not
part of the editor.

**Identify** briefly shows the display number and connector on each screen,
without taking focus or intercepting input.

Keyboard: arrows move the selected display by 20 logical pixels, Shift + arrow
by 1 pixel; Alt + arrow aligns above/left/right/below. Tab focuses controls;
Escape closes the panel and cancels any active preview. Disconnected or
overlapping layouts cannot be applied.

## Persistence and rollback

Nothing is written to the monitor configuration when editing or starting a
preview. A user-session systemd timer is armed and confirmed active **before**
any runtime mutation; it is independent of the panel and shell process.

Keep writes `~/.config/hypr/omarchy-display-layout.lua` and a delimited `dofile`
block at the end of `~/.config/hypr/monitors.lua`. Existing bytes outside that
block are preserved. Output selectors are reused exactly, including the
monitor description behind a dock. Position-only `hl.monitor` overrides rely
on Hyprland 0.56's same-selector merge behavior.

The helper verifies compositor state, config hashes and monitor identity before
Apply and Keep. A second request, changed dock configuration or newer external
edit is not silently overwritten. Errors are reported; failed rollback retains
private recovery state instead of claiming success. Recovery and one recent
result live under `~/.local/state/omarchy-display-layout/`.

## Scope

- Hyprland 0.56 Lua, native Omarchy bar, Python 3 standard library and user systemd.
- The existing `monitors.lua` needs a **literal per-display output rule**.
  Dynamic/computed output selectors, ambiguous connector + description rules,
  symlinked managed files and mirror layouts are deliberately refused.
- The canvas supports multiple active displays and mixed logical dimensions.
- No resolution/scaling controls, profile daemon, auto-dock switching or
  changes to workspace assignments, brightness, accounts or system services.
- Existing Tusche/Chrom/Platin popup material is reused when installed;
  otherwise native Omarchy popup styling is used.

## Install

### Existing cloned display widget (recommended here)

This repository includes an adapter for `nerdibeard.monitor`, the user's existing
clone of Omarchy's monitor widget. It embeds the same reusable `Editor.qml`
inside the original native popup. No second widget is enabled and no bar layout
settings are rewritten.

```sh
./dev-install.sh --integrate-monitor
```

The adapter requires the existing clone at
`~/.config/omarchy/plugins/nerdibeard.monitor`. It preserves brightness and the
native display controls and replaces the old immediate-save arrangement helper.
The old helper and arrangement files may remain on disk but are no longer called.
The installer backs up the entire previous clone under
`~/.local/state/omarchy-display-layout/monitor-backups/` before installing.
Review/port the adapter if your cloned display widget has additional custom edits;
installation replaces its `Panel.qml`, `Model.js` and manifest with this version.
The adapter derives from Omarchy's MIT-licensed monitor widget.

The host owns popup placement, theme material, focus and open/close lifecycle.
Only the instance that started a preview owns close-to-revert; a closed panel on
another monitor must not cancel it. Reopening via IPC during a preview routes to
its owner. Runtime output rebuilds recover that owner in the recreated popup;
a full shell restart still relies on the independent watchdog. `display-layout open/state/preset/reset/close`
IPC selects the focused monitor's host. Opening through that IPC selects Layout.

User plugins can remain cached after rescan; if the installed editor is not yet
loaded, use `omarchy restart shell`. This restarts only the shell, not Hyprland.
A restart also makes the user's already-saved plugin enable/disable settings
effective; the installer itself does not change them.

To restore the previous display widget, copy the backed-up `Panel.qml`, `Model.js`
and `manifest.json` into `nerdibeard.monitor`, rescan, and restart the shell if
needed. Saved monitor arrangements are unaffected by restoring the UI.

### Standalone bar button

For installations without this clone, install only the editor and enable its
standalone button:

```sh
./dev-install.sh
omarchy plugin enable nerdibeard.display-layout --section right
```

The embedded adapter requires the installed `nerdibeard.display-layout` folder.
**Disable its standalone button, do not remove that folder.** The integrated
installer refuses installation if the standalone bar button is enabled.
Do not enable the standalone button together with the embedded adapter. Disable
an existing standalone button with:

```sh
omarchy plugin disable nerdibeard.display-layout
```

Disabling the button does not remove a layout you explicitly kept. To stop using
the saved override, back up `monitors.lua`, remove only the delimited
`OMARCHY DISPLAY LAYOUT` block, then reload and check `hyprctl configerrors`.

## Verification

```sh
node tests/model.cjs
node tests/editor.cjs
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 display_layout.py snapshot
```

The automated checks use a mocked compositor for mutations. The plugin can be
opened, dragged, preset, identified and visually inspected on a working desktop
without pressing Apply. The controller lifecycle tests also cover
closed instances on a second output, closing while Apply is in flight, stale
drafts and clearing preview ownership. Automated checks are not proof that a
real physical rearrangement has been accepted by the user.

## Design references

Existing options were checked before implementation:
[nwg-displays](https://github.com/nwg-piotr/nwg-displays) provides a separate GTK
display editor, while this plugin keeps the requested interaction in the
existing bar and shares its theme. Native component contracts come from the
installed Omarchy `Ui/Panel.qml`, `Ui/KeyboardPanel.qml` and plugin documentation.

Licensed under MIT.
