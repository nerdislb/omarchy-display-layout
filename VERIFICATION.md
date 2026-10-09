# Integration verification — 2026-10-09

Version 0.2.0 integrates the reusable editor into the existing cloned
`nerdibeard.monitor` popup. The existing Tusche System-group entry opens it;
there is no additional enabled bar button.

## Automated evidence

| Check | Result |
| --- | --- |
| Python backend | 32 tests passed |
| Logical geometry / snapping | 11 checks passed |
| Editor lifecycle / registry | 10 checks passed |
| QML parser and plugin manifests | Passed |
| Installer shell syntax and Git whitespace | Passed |

Lifecycle cases include closed non-owner panels, closing during Apply, output
instance replacement, exact-output recovery without a fallback owner, stale
cancellation tokens, and IPC reopening during a preview. Backend cases include
unrelated Lua edits during a preview, position-only rollback without overwriting
newer files, and a helper lost after runtime Apply but before saving its phase.

## Live evidence

The delivered adapter was loaded in the running native Omarchy shell with its
existing theme material. Checked drag-and-drop, keyboard position changes and
Tab focus, tab switching, Identify, Apply, Keep, Revert, and the independent
20-second user-systemd watchdog. After an output geometry rebuild, the new popup
recovered the preview and showed usable Keep/Revert controls with a countdown.
Keep saved and verified a test position without changing the external 180 Hz
mode. The test-only override was then removed with an ownership check; original
`monitors.lua` and `shell.json` were byte-identical to their pre-test copies.
Hyprland reported no configuration errors.

The laptop lid was closed during live acceptance, so only the external output
was active. **Two simultaneously active physical displays, mixed DPI, rotation
and pointer crossing between outputs have not been live-accepted in this round.**
They are covered by the geometry and lifecycle checks, not a physical test.

## Independent reviews

Native Claude Code (`claude-opus-5-5`) and Antigravity
(`claude-opus-5-5-high`) reviewed isolated source snapshots using existing
subscriptions, without editing the installed plugin. Findings drove fixes for
Tab focus, default display controls, runtime rollback after unrelated Lua edits,
preview-owner routing, exact-output recovery and installer ordering. Follow-up
review findings about output-rebuild races were addressed with targeted lifecycle
checks. Runtime observations, not reviewer consensus, establish the live claims
above.
