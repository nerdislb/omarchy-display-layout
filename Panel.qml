import QtQuick
import Quickshell
import qs.Ui as Ui
import qs.Commons

Ui.Panel {
  id: root
  moduleName: "nerdibeard.display-layout"
  manageIpc: false
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  property var materialAdapter: null
  Component.onCompleted: {
    var component = Qt.createComponent("file://" + Quickshell.env("HOME") + "/.config/omarchy/plugins/nerdibeard.tusche-bar/NativeMaterial.qml");
    if (component.status === Component.Ready) {
      materialAdapter = component.createObject(root);
      Qt.callLater(function() { if (materialAdapter) materialAdapter.attach(root); });
    }
  }
  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    active: root.opened
    text: "󰍺"
    onPressed: root.toggle()
  }
  Ui.KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: editor.focusItem
    contentWidth: fittedContentWidth(Style.space(660))
    contentHeight: fittedContentHeight(editor.implicitHeight, Style.space(720))
    Flickable {
      anchors.fill: parent
      contentWidth: width
      contentHeight: editor.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height
      Editor {
        id: editor
        width: parent.width
        height: implicitHeight
        host: root
        opened: root.opened
        screenName: panel.screen ? panel.screen.name : ""
        cardGeometry: ({ x: panel.cardOrigin.x, y: panel.cardOrigin.y, width: panel.contentWidth, height: panel.contentHeight })
      }
    }
  }
}
