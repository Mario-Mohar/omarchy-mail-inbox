import QtQuick
import qs.Commons
import qs.Ui

// One pill for every mailbox together. The per-account breakdown lives in the
// panel, so adding a tenth address costs no bar space.
BarWidget {
  id: root
  moduleName: "themo.mail-inbox"

  Service {
    id: mail
    settings: root.settings
  }

  readonly property string glyphUnread: "󰇮"
  readonly property string glyphRead: "󰇯"

  readonly property bool hideWhenEmpty: mail.boolSetting("hideWhenEmpty", false)

  readonly property string barText: {
    if (!mail.configured) return glyphUnread + " ?"
    var glyph = mail.totalUnread > 0 ? glyphUnread : glyphRead
    if (root.vertical) return mail.totalUnread > 0 ? String(mail.totalUnread) : glyph
    return mail.totalUnread > 0 ? glyph + " " + mail.totalUnread : glyph
  }

  readonly property string tooltip: {
    if (!mail.configured)
      return "Mail Inbox — no mailbox configured yet.\nClick and use “Add a mailbox”."
    var lines = []
    for (var i = 0; i < mail.accounts.length; i++) {
      var a = mail.accounts[i]
      lines.push(a.error && a.error !== ""
        ? a.label + " — " + a.error
        : a.label + " — " + (a.unread > 0 ? a.unread : "clear"))
    }
    return lines.join("\n")
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("service" in target) target.service = mail
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function refresh() { mail.refresh() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  visible: !hideWhenEmpty || mail.totalUnread > 0 || !mail.configured || mail.erroredCount > 0

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  // WidgetButton, not BarIconButton: the latter pins itself to a fixed glyph
  // slot, so a label with a count overflows into the next widget.
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barText
    labelVisible: true
    tooltipText: root.tooltip
    active: mail.totalUnread > 0 || mail.erroredCount > 0 || !mail.configured
    dimmed: mail.erroredCount > 0 && mail.totalUnread === 0 && mail.everLoaded

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.refresh()
      else root.togglePanel()
    }

    onWheelMoved: function(delta) { root.refresh() }
  }
}
