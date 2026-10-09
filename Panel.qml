import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "Model.js" as Model
import "Registry.js" as Registry

Ui.Panel {
  id: root
  moduleName: "nerdibeard.display-layout"
  manageIpc: false
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property string helper: Qt.resolvedUrl("display_layout.py").toString().replace(/^file:\/\//, "")
  readonly property string screenName: button.QsWindow.window && button.QsWindow.window.screen ? button.QsWindow.window.screen.name : ""
  property bool ipcLeader: false
  property var live: []
  property var draft: []
  property var pending: null
  property string selected: ""
  property string anchorName: ""
  property string errorMessage: ""
  property string notice: ""
  property bool stale: false
  property bool identifying: false
  property var frozenBounds: null
  property var materialAdapter: null
  property string action: ""
  property string baselineRevision: ""
  readonly property bool busy: actionProc.running
  readonly property bool dirty: Model.changed(draft, live)
  readonly property string invalid: Model.validate(draft)
  readonly property var selectedDisplay: draft.filter(function(d) { return d.name === root.selected; })[0] || null
  readonly property var anchorDisplay: draft.filter(function(d) { return d.name === root.anchorName; })[0] || null
  readonly property var world: frozenBounds || Model.bounds(draft)

  Component.onCompleted: {
    Registry.register(root);
    refresh();
    // Reuse the user's installed Tusche/Chrom/Platin popup material without
    // requiring it on other installations or changing its code.
    var component = Qt.createComponent("file://" + Quickshell.env("HOME") + "/.config/omarchy/plugins/nerdibeard.tusche-bar/NativeMaterial.qml");
    if (component.status === Component.Ready) {
      materialAdapter = component.createObject(root);
      Qt.callLater(function() { if (materialAdapter) materialAdapter.attach(root); });
    }
  }
  Component.onDestruction: Registry.unregister(root)
  onOpenedChanged: {
    if (opened) { errorMessage = ""; notice = ""; refresh(); }
    else {
      identifying = false;
      if (pending && !busy) runAction("revert", pending.token);
    }
  }

  function focusedInstance() {
    return Registry.onScreen(Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : root.screenName);
  }
  function replaceDraft(list) {
    draft = Model.copy(list);
    frozenBounds = null;
    stale = false;
    if (!draft.some(function(d) { return d.name === root.selected; })) {
      selected = (draft.filter(function(d) { return !/^eDP|^LVDS|^DSI/.test(d.name); })[0] || draft[0] || {}).name || "";
    }
    chooseAnchor();
  }
  function chooseAnchor() {
    var others = draft.filter(function(d) { return d.name !== root.selected; });
    if (!others.some(function(d) { return d.name === root.anchorName; }))
      anchorName = (others.filter(function(d) { return /^(eDP|LVDS|DSI)/.test(d.name); })[0] || others[0] || {}).name || "";
  }
  function select(name) { selected = name; chooseAnchor(); }
  function refresh() { if (!queryProc.running && !busy) queryProc.running = true; }
  function receive(data, mutation) {
    if (!data.ok) { errorMessage = String(data.error || "Could not read display state."); return; }
    var incoming = Model.active(data.monitors || []);
    if (live.length && dirty && !mutation && !pending
        && (Model.topology(incoming) !== Model.topology(live) || Model.changed(incoming, live)
          || (data.revision && baselineRevision && data.revision !== baselineRevision))) stale = true;
    var wasPending = !!pending;
    var wasDirty = dirty;
    live = incoming;
    pending = data.pending || null;
    if (mutation || !draft.length || (!wasDirty && !stale) || (wasPending && !pending)) {
      replaceDraft(incoming);
      baselineRevision = data.revision || "";
    }
    if (pending && !opened && !busy) Qt.callLater(function() { if (root.pending && !root.opened && !root.busy) root.runAction("revert", root.pending.token); });
  }
  function runAction(verb, argument) {
    if (busy) return;
    action = verb;
    errorMessage = "";
    actionProc.command = ["python3", helper, verb, String(argument || "")];
    if (verb === "preview" && baselineRevision) actionProc.command = actionProc.command.concat([baselineRevision]);
    actionProc.running = true;
  }
  function apply() {
    if (!dirty || pending || stale || invalid || busy) return;
    runAction("preview", JSON.stringify(draft.map(function(d) { return { name: d.name, x: d.x, y: d.y }; })));
  }
  function arrange(direction) {
    if (pending || busy || !selected || !anchorName) return;
    draft = Model.preset(draft, selected, anchorName, direction);
    frozenBounds = null;
    notice = "Draft only — Apply to test on your displays.";
  }
  function resetDraft() { if (!pending && !busy) { replaceDraft(live); notice = ""; errorMessage = ""; refresh(); } }
  function moveSelected(dx, dy) {
    if (!selectedDisplay || pending || busy) return;
    draft = Model.position(draft, selected, selectedDisplay.x + dx, selectedDisplay.y + dy);
  }
  function state() {
    return JSON.stringify({ opened: opened, screen: screenName, live: live, draft: draft, pending: pending,
      selected: selected, anchor: anchorName, dirty: dirty, stale: stale, invalid: invalid, error: errorMessage,
      identifying: identifying, busy: busy, revision: baselineRevision,
      card: { x: panel.cardOrigin.x, y: panel.cardOrigin.y, width: panel.contentWidth, height: panel.contentHeight } });
  }
  ShellIpc {
    target: "display-layout"
    enabled: root.ipcLeader
    function open(): void { Registry.open(Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : root.screenName); }
    function close(): void { var item = Registry.anyOpen(); if (item) item.close(); }
    function state(): string { var item = Registry.anyOpen() || root.focusedInstance(); return item ? item.state() : "{}"; }
    function preset(direction: string): void { var item = Registry.anyOpen(); if (item) item.arrange(direction); }
    function reset(): void { var item = Registry.anyOpen(); if (item) item.resetDraft(); }
    function identify(): void { var item = Registry.anyOpen(); if (item) item.identifying = !item.identifying; }
  }
  Process {
    id: queryProc
    command: ["python3", root.helper, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.receive(JSON.parse(text), false); }
        catch (e) { root.errorMessage = "Display helper returned no usable state."; }
      }
    }
  }
  Process {
    id: actionProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text);
          root.receive(data, true);
          if (data.ok) root.notice = root.action === "keep" ? "Layout saved. Your other display settings are unchanged."
            : root.action === "revert" ? "Previous layout restored." : "Test the pointer crossing, then Keep or Revert.";
        } catch (e) { root.errorMessage = "Display action failed. The independent rollback remains responsible for any active preview."; }
      }
    }
    onExited: function(code, status) { Qt.callLater(root.refresh); }
  }
  Timer { interval: root.pending ? 500 : 3000; running: root.opened || !!root.pending; repeat: true; onTriggered: root.refresh() }
  Timer { interval: 2500; running: root.identifying; onTriggered: root.identifying = false }

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    active: root.opened
    iconComponent: Component {
      Item {
        Rectangle { x: 4; y: 2; width: 18; height: 10; radius: 1; color: "transparent"; border.width: 1.5; border.color: root.barForeground }
        Rectangle { x: 8; y: 15; width: 10; height: 7; radius: 1; color: "transparent"; border.width: 1.5; border.color: root.barForeground }
        Rectangle { x: 6; y: 24; width: 14; height: 1.5; color: root.barForeground }
      }
    }
    onPressed: function(b) { root.toggle(); }
  }
  Ui.KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: fittedContentWidth(Style.space(660))
    contentHeight: fittedContentHeight(content.implicitHeight, Style.space(720))

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true
      Keys.priority: Keys.BeforeItem
      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) { root.close(); event.accepted = true; return; }
        var dx = event.key === Qt.Key_Left ? -1 : event.key === Qt.Key_Right ? 1 : 0;
        var dy = event.key === Qt.Key_Up ? -1 : event.key === Qt.Key_Down ? 1 : 0;
        if (dx || dy) {
          if (event.modifiers & Qt.AltModifier) root.arrange(dx < 0 ? "left" : dx > 0 ? "right" : dy < 0 ? "above" : "below");
          else root.moveSelected(dx * ((event.modifiers & Qt.ShiftModifier) ? 1 : 20), dy * ((event.modifiers & Qt.ShiftModifier) ? 1 : 20));
          event.accepted = true;
        }
      }
      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        Column {
          id: content
          width: parent.width
          spacing: Style.space(12)

          Row {
            width: parent.width; spacing: Style.space(12)
            Text { textFormat: Text.PlainText; text: "󰍺"; color: Commons.Color.popups.text; font.family: Style.font.family; font.pixelSize: Style.font.display }
            Column {
              width: parent.width - Style.space(160); spacing: Style.space(3)
              Label { text: "Display layout"; font.pixelSize: Style.font.title; font.bold: true }
              Label { text: "Match the screens on your desk."; color: Util.alpha(Commons.Color.popups.text, 0.65) }
            }
            Ui.Button { text: "Identify"; focusable: true; selected: root.identifying; enabled: !root.pending; onClicked: root.identifying = !root.identifying }
          }

          Rectangle {
            id: canvas
            width: parent.width; height: Style.space(290)
            radius: Style.cornerRadius
            color: Util.alpha(Commons.Color.popups.text, 0.045)
            border.color: Util.alpha(Commons.Color.popups.text, 0.12)
            clip: true
            readonly property real fit: Math.min((width - Style.space(70)) / Math.max(root.world.width, 1), (height - Style.space(66)) / Math.max(root.world.height, 1))
            readonly property real originX: (width - root.world.width * fit) / 2 - root.world.x * fit
            readonly property real originY: (height - root.world.height * fit) / 2 - root.world.y * fit
            Repeater {
              model: root.draft.length
              Rectangle {
                id: tile
                required property int index
                readonly property var display: root.draft[index] || ({})
                x: canvas.originX + (display.x || 0) * canvas.fit
                y: canvas.originY + (display.y || 0) * canvas.fit
                width: (display.logicalWidth || 1) * canvas.fit
                height: (display.logicalHeight || 1) * canvas.fit
                radius: Style.cornerRadius
                color: display.name === root.selected ? Util.alpha(Commons.Color.accent, 0.20) : Util.alpha(Commons.Color.popups.text, 0.085)
                border.width: display.name === root.selected ? 2 : 1
                border.color: display.name === root.selected ? Commons.Color.accent : Util.alpha(Commons.Color.popups.text, 0.35)
                z: dragArea.pressed ? 10 : 1
                Column {
                  anchors.centerIn: parent; width: parent.width - Style.space(14); spacing: Style.space(3)
                  Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: String(tile.index + 1); font.pixelSize: Style.font.title; font.bold: true }
                  Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: tile.display.label || tile.display.name || ""; font.bold: true; elide: Text.ElideRight }
                  Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: tile.display.name || ""; font.pixelSize: Style.font.caption; color: Util.alpha(Commons.Color.popups.text, 0.6) }
                }
                MouseArea {
                  id: dragArea
                  anchors.fill: parent
                  enabled: !root.pending && !root.busy
                  cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                  preventStealing: true
                  property point pressedAt
                  property real startX: 0
                  property real startY: 0
                  onPressed: function(mouse) {
                    root.select(tile.display.name);
                    keyCatcher.forceActiveFocus();
                    root.frozenBounds = Model.bounds(root.draft);
                    pressedAt = mapToItem(canvas, mouse.x, mouse.y);
                    startX = tile.display.x; startY = tile.display.y;
                  }
                  onPositionChanged: function(mouse) {
                    if (!pressed) return;
                    var point = mapToItem(canvas, mouse.x, mouse.y);
                    root.draft = Model.snap(root.draft, root.selected, startX + (point.x - pressedAt.x) / canvas.fit,
                      startY + (point.y - pressedAt.y) / canvas.fit, Style.space(12) / canvas.fit);
                  }
                  onReleased: { root.frozenBounds = null; root.notice = "Draft only — Apply to test on your displays."; }
                  onCanceled: root.frozenBounds = null
                }
              }
            }
            Label { anchors.bottom: parent.bottom; anchors.bottomMargin: Style.space(8); anchors.horizontalCenter: parent.horizontalCenter;
              text: root.pending ? "LIVE PREVIEW" : root.dirty ? "DRAFT · NOT APPLIED" : "CURRENT LAYOUT";
              font.pixelSize: Style.font.caption; font.letterSpacing: 1; color: Util.alpha(Commons.Color.popups.text, 0.5) }
          }

          Row {
            spacing: Style.space(5)
            Repeater {
              model: root.draft.length
              Ui.Button {
                required property int index
                readonly property var display: root.draft[index] || ({})
                text: (index + 1) + " · " + (display.label || display.name || "")
                fontSize: Style.font.bodySmall
                focusable: true; selected: display.name === root.selected
                enabled: !root.pending && !root.busy
                onClicked: root.select(display.name)
              }
            }
          }
          Label {
            width: parent.width; wrapMode: Text.Wrap
            text: root.selectedDisplay ? root.selectedDisplay.name + " · " + root.selectedDisplay.width + " × " + root.selectedDisplay.height + " · " + Math.round(root.selectedDisplay.refreshRate) + " Hz · " + root.selectedDisplay.scale + "× scale · position " + root.selectedDisplay.x + ", " + root.selectedDisplay.y : "Reading displays…"
            color: Util.alpha(Commons.Color.popups.text, 0.6); font.pixelSize: Style.font.bodySmall
          }
          Row {
            spacing: Style.space(5); visible: root.draft.length > 1
            Repeater {
              model: ["above", "left", "right", "below"]
              Ui.Button {
                required property string modelData
                text: modelData === "above" && /^(eDP|LVDS|DSI)/.test(root.anchorName) ? "Above laptop"
                  : modelData.charAt(0).toUpperCase() + modelData.slice(1)
                focusable: true; bordered: true; enabled: !root.pending && !root.busy
                onClicked: root.arrange(modelData)
              }
            }
            Ui.Button { text: "Reset"; focusable: true; enabled: !root.pending && !root.busy; onClicked: root.resetDraft() }
          }
          Row {
            visible: root.draft.length > 2; spacing: Style.space(5)
            Label { text: "Relative to:"; anchors.verticalCenter: parent.verticalCenter }
            Repeater {
              model: root.draft.length
              Ui.Button {
                required property int index
                readonly property var display: root.draft[index] || ({})
                visible: display.name !== root.selected; text: display.label || display.name || ""
                selected: display.name === root.anchorName; focusable: true
                enabled: !root.pending && !root.busy; onClicked: root.anchorName = display.name
              }
            }
          }
          Label {
            width: parent.width; wrapMode: Text.Wrap
            color: root.errorMessage || root.stale || (root.dirty && root.invalid) ? Commons.Color.urgent : Commons.Color.popups.text
            text: root.errorMessage || (root.stale ? "Displays changed while editing. Reset before applying." : "")
              || (root.dirty && root.invalid ? root.invalid : "") || root.notice || "Drag a screen and join its edge to another. Resolution, refresh rate and scaling stay unchanged."
          }
          Rectangle { width: parent.width; height: 1; color: Util.alpha(Commons.Color.popups.text, 0.15) }
          Row {
            width: parent.width; spacing: Style.space(8)
            Ui.Button {
              visible: !root.pending; text: root.busy ? "Applying…" : "Apply"; focusable: true; bordered: true
              enabled: root.dirty && !root.invalid && !root.stale && !root.busy
              onClicked: root.apply()
            }
            Ui.Button { visible: !!root.pending; text: "Keep"; focusable: true; bordered: true; enabled: !root.busy; onClicked: root.runAction("keep", root.pending.token) }
            Ui.Button { visible: !!root.pending; text: "Revert"; focusable: true; enabled: !root.busy; onClicked: root.runAction("revert", root.pending.token) }
            Label {
              anchors.verticalCenter: parent.verticalCenter
              text: root.pending ? "Reverting in " + root.pending.remainingSeconds + " s unless kept."
                : root.dirty ? "Apply → test → Keep to save" : "No changes pending"
              color: Util.alpha(Commons.Color.popups.text, 0.6); font.pixelSize: Style.font.bodySmall
            }
          }
          Label { width: parent.width; wrapMode: Text.Wrap; text: "Arrows: move · Shift: 1 px · Alt + arrows: align · Esc: close / revert";
            font.pixelSize: Style.font.caption; color: Util.alpha(Commons.Color.popups.text, 0.45) }
        }
      }
    }
  }

  Variants {
    model: root.identifying && root.opened ? Quickshell.screens : []
    delegate: Component {
      PanelWindow {
        required property var modelData
        screen: modelData
        visible: true
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "display-layout-identify"
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        anchors { top: true; left: true; right: true }
        implicitHeight: Style.space(110)
        mask: Region { width: 0; height: 0 }
        Rectangle {
          anchors.horizontalCenter: parent.horizontalCenter; y: Style.space(40)
          width: badge.implicitWidth + Style.space(34); height: badge.implicitHeight + Style.space(20)
          radius: Style.cornerRadius; color: Commons.Color.popups.background
          border.color: Commons.Color.accent; border.width: 2
          Label { id: badge; anchors.centerIn: parent; font.bold: true; font.pixelSize: Style.font.title;
            text: { var i = root.draft.findIndex(function(d) { return d.name === modelData.name; }); return (i >= 0 ? (i + 1) + " · " : "") + modelData.name; } }
        }
      }
    }
  }
  component Label: Text {
    textFormat: Text.PlainText
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    color: Commons.Color.popups.text
    renderType: Text.NativeRendering
  }
}
