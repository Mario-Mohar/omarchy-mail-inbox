import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Two panes: the mailbox list on the left, the selected mailbox's unread mail
// on the right. Adding addresses grows this list, not the bar.
//
// The right pane has two modes. The list is the default; clicking a message
// swaps it for the full text plus a reply box. Reading never changes the
// mailbox — \Seen is set only by the "Mark read" button — so the bar counter
// cannot drop just because something was glanced at.
Panel {
  id: root
  moduleName: "themo.mail-inbox"
  ipcTarget: "themo.mail-inbox"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  readonly property var barIdentity: hostWidget || root

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.5)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color accent: Color.accent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var accounts: service ? service.accounts : []
  readonly property int totalUnread: service ? service.totalUnread : 0
  readonly property bool configured: service ? service.configured : false

  readonly property var detail: service ? service.detail : null
  readonly property bool detailLoading: service ? service.detailLoading : false
  readonly property string detailError: service ? service.detailError : ""
  readonly property bool detailMode: detail !== null || detailLoading
    || (detailError !== "" && service && service.detailKey !== "")
  readonly property bool sending: service ? service.sending : false

  // Tracked by id, not index: a refresh may reorder or drop mailboxes and the
  // selection must not silently jump to a different inbox.
  property string selectedId: ""

  // Reply composer state. Cleared whenever a different message is opened.
  property string replyBody: ""
  property bool replyAll: false

  readonly property int selectedIndex: {
    if (!accounts.length) return -1
    for (var i = 0; i < accounts.length; i++)
      if (accounts[i].id === selectedId) return i
    return 0
  }
  readonly property var selected: selectedIndex >= 0 ? accounts[selectedIndex] : null

  onAccountsChanged: {
    if (selectedId === "" && accounts.length > 0) selectedId = accounts[0].id
  }

  function open() { root.controller.show(); refresh() }
  function close() { closeDetail(); root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }

  // Lets the panel be opened from a key binding or a script, the way the other
  // Omarchy panels can be. manageIpc is false because the Panel base type does
  // not register the target itself, so this block does it.
  //   omarchy shell themo.mail-inbox toggle
  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
  }
  function refresh() { if (service) service.refresh() }

  function openDetail(uid) {
    if (!service || !root.selected) return
    root.replyBody = ""
    root.replyAll = false
    service.openMessage(root.selected.id, uid)
  }

  function closeDetail() {
    root.replyBody = ""
    root.replyAll = false
    if (service) service.closeMessage()
  }

  function markRead() {
    if (!service || !detail) return
    service.markSeen(detail.account, detail.uid)
    closeDetail()
  }

  readonly property var replyRecipients: detail && detail.replyTo ? detail.replyTo : []
  readonly property var replyCc: replyAll && detail && detail.replyAllCc ? detail.replyAllCc : []

  readonly property string replySubject: {
    if (!detail) return ""
    var s = String(detail.subject || "")
    return /^\s*(re|aw|antw)\s*:/i.test(s) ? s : "Re: " + s
  }

  readonly property bool canSend: detail !== null && !sending
    && replyRecipients.length > 0 && replyBody.trim() !== ""

  function sendReply() {
    if (!canSend) return
    service.sendReply({
      "account": detail.account,
      "uid": detail.uid,
      "to": replyRecipients,
      "cc": replyCc,
      "subject": replySubject,
      "body": replyBody + "\n\n" + quotedOriginal(),
      "inReplyTo": detail.messageId,
      "references": detail.references,
      "markAnswered": true
    })
  }

  // Plain ">" quoting: the reply is text/plain, and every client renders this.
  function quotedOriginal() {
    if (!detail) return ""
    var stamp = detail.date ? new Date(detail.date) : null
    var intro = "On " + (stamp && !isNaN(stamp.getTime())
      ? Qt.formatDateTime(stamp, "d MMM yyyy 'at' HH:mm") : "an earlier date")
      + ", " + String(detail.from || "") + " wrote:"
    var lines = String(detail.body || "").split("\n")
    var quoted = []
    for (var i = 0; i < lines.length && i < 500; i++) quoted.push("> " + lines[i])
    return intro + "\n" + quoted.join("\n")
  }

  Connections {
    target: root.service
    function onReplySent() { root.closeDetail() }
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  // Up/Down cycle mailboxes without reaching for the mouse.
  function cycleAccount(step) {
    if (!accounts.length) return
    var next = (selectedIndex + step + accounts.length) % accounts.length
    selectedId = accounts[next].id
  }

  // Header plus the taller of the two panes, clamped. Reading a mail needs a
  // taller panel than scanning subject lines, so detail mode asks for more.
  readonly property int desiredHeight: {
    if (detailMode) return Style.space(520)
    var rows = Math.max(accounts.length,
                        selected && selected.messages ? selected.messages.length * 2 : 0)
    var body = Style.space(24) * Math.max(3, Math.min(rows, 12))
    return Math.max(Style.space(170), Math.min(Style.space(470), body + Style.space(70)))
  }

  function whenText(iso) {
    if (!iso) return ""
    var when = new Date(iso)
    if (isNaN(when.getTime())) return ""
    var now = new Date()
    var sameDay = when.getFullYear() === now.getFullYear()
      && when.getMonth() === now.getMonth()
      && when.getDate() === now.getDate()
    return sameDay ? Qt.formatDateTime(when, "HH:mm") : Qt.formatDateTime(when, "d MMM")
  }

  function fullWhenText(iso) {
    if (!iso) return ""
    var when = new Date(iso)
    if (isNaN(when.getTime())) return ""
    return Qt.formatDateTime(when, "d MMM yyyy, HH:mm")
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(620))
    // Grows with whichever pane is taller, within bounds: a single unread
    // mail should not open a half-empty panel, and forty should not run off
    // the screen.
    contentHeight: panel.fittedContentHeight(root.desiredHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // Escape backs out of the open mail first and only then shuts the panel:
      // losing a half-written reply to a stray Escape would be worse.
      onCloseRequested: root.detailMode ? root.closeDetail() : root.close()
      onReturnRequested: if (!root.detailMode) root.refresh()
      onTabRequested: function(direction) { if (!root.detailMode) root.switchPanel(direction) }
      Keys.onUpPressed: if (!root.detailMode) root.cycleAccount(-1)
      Keys.onDownPressed: if (!root.detailMode) root.cycleAccount(1)

      Column {
        anchors.fill: parent
        spacing: Style.space(8)

        // -------------------------------------------------------- header
        Item {
          width: parent.width
          height: Math.max(headline.implicitHeight, refreshButton.implicitHeight) + Style.space(6)

          Row {
            id: headline
            anchors.left: parent.left
            anchors.leftMargin: Style.space(14)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(8)

            Text {
              // Mail decides what is in these strings. Without this Qt guesses
              // whether the text is rich text and would render markup from a subject.
              textFormat: Text.PlainText
              text: "Mail"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
              anchors.verticalCenter: parent.verticalCenter
            }

            Text {
              // Mail decides what is in these strings. Without this Qt guesses
              // whether the text is rich text and would render markup from a subject.
              textFormat: Text.PlainText
              text: {
                if (!root.configured) return "no mailbox yet"
                return root.totalUnread === 0 ? "all clear"
                  : root.totalUnread + " unread across " + root.accounts.length
                    + (root.accounts.length === 1 ? " mailbox" : " mailboxes")
              }
              color: root.totalUnread > 0 ? root.urgent : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              anchors.verticalCenter: parent.verticalCenter
            }
          }

          PanelActionButton {
            id: refreshButton
            anchors.right: parent.right
            anchors.rightMargin: Style.space(14)
            anchors.verticalCenter: parent.verticalCenter
            iconText: "󰑐"
            tooltipText: "Check all mailboxes now"
            enabled: !(root.service && root.service.polling) && !root.detailMode
            onClicked: root.refresh()
          }
        }

        PanelSeparator { width: parent.width }

        // ------------------------------------------------- nothing set up
        Item {
          width: parent.width
          height: root.configured ? 0 : Style.space(120)
          visible: !root.configured

          Column {
            anchors.centerIn: parent
            spacing: Style.space(10)

            Text {
              // Mail decides what is in these strings. Without this Qt guesses
              // whether the text is rich text and would render markup from a subject.
              textFormat: Text.PlainText
              text: "No mailbox configured yet."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              anchors.horizontalCenter: parent.horizontalCenter
            }

            Button {
              text: "Add a mailbox"
              anchors.horizontalCenter: parent.horizontalCenter
              onClicked: { if (root.service) root.service.openSetup(); root.close() }
            }
          }
        }

        // ------------------------------------------- mailbox list | mail
        Item {
          width: parent.width
          height: parent.height - y
          visible: root.configured

          // ---- left: mailboxes
          Flickable {
            id: accountScroll
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: Style.space(180)
            contentWidth: width
            contentHeight: accountColumn.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height

            Column {
              id: accountColumn
              width: accountScroll.width

              Repeater {
                model: root.accounts

                Rectangle {
                  width: accountColumn.width
                  height: accountRow.implicitHeight + Style.space(10)
                  color: modelData.id === root.selectedId
                    ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.14)
                    : "transparent"
                  radius: Style.space(4)
                  opacity: root.detailMode && modelData.id !== root.selectedId ? 0.45 : 1.0

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    // Switching mailbox with a mail open would leave the
                    // reader showing something the list no longer contains.
                    onClicked: {
                      if (modelData.id === root.selectedId) return
                      root.closeDetail()
                      root.selectedId = modelData.id
                    }
                  }

                  Item {
                    id: accountRow
                    anchors.left: parent.left
                    anchors.leftMargin: Style.space(14)
                    anchors.right: parent.right
                    anchors.rightMargin: Style.space(10)
                    anchors.verticalCenter: parent.verticalCenter
                    implicitHeight: nameText.implicitHeight

                    Text {
                      // Mail decides what is in these strings. Without this Qt guesses
                      // whether the text is rich text and would render markup from a subject.
                      textFormat: Text.PlainText
                      id: nameText
                      anchors.left: parent.left
                      anchors.right: countText.left
                      anchors.rightMargin: Style.space(6)
                      text: modelData.label
                      elide: Text.ElideRight
                      color: modelData.id === root.selectedId ? root.foreground : root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: modelData.id === root.selectedId
                    }

                    Text {
                      // Mail decides what is in these strings. Without this Qt guesses
                      // whether the text is rich text and would render markup from a subject.
                      textFormat: Text.PlainText
                      id: countText
                      anchors.right: parent.right
                      text: modelData.error && modelData.error !== ""
                        ? "!" : (modelData.unread > 0 ? String(modelData.unread) : "")
                      color: root.urgent
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: true
                    }
                  }
                }
              }
            }
          }

          Rectangle {
            id: divider
            anchors.left: accountScroll.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 1
            color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)
          }

          // ---- right: unread mail of the selected mailbox
          Flickable {
            id: mailScroll
            anchors.left: divider.right
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            contentWidth: width
            contentHeight: mailColumn.implicitHeight
            clip: true
            visible: !root.detailMode
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height

            Column {
              id: mailColumn
              width: mailScroll.width

              Text {
                // Mail decides what is in these strings. Without this Qt guesses
                // whether the text is rich text and would render markup from a subject.
                textFormat: Text.PlainText
                width: parent.width - Style.space(28)
                x: Style.space(14)
                topPadding: Style.space(10)
                visible: root.selected && root.selected.error !== ""
                wrapMode: Text.WordWrap
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                text: root.selected ? root.selected.error : ""
              }

              Text {
                // Mail decides what is in these strings. Without this Qt guesses
                // whether the text is rich text and would render markup from a subject.
                textFormat: Text.PlainText
                width: parent.width - Style.space(28)
                x: Style.space(14)
                topPadding: Style.space(10)
                visible: root.selected && root.selected.error === ""
                  && root.selected.messages.length === 0
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                text: root.service && root.service.everLoaded ? "Nothing unread." : "Checking…"
              }

              Repeater {
                model: root.selected && root.selected.error === "" ? root.selected.messages : []

                Rectangle {
                  width: mailColumn.width
                  height: messageBody.implicitHeight + Style.space(12)
                  color: messageHover.containsMouse
                    ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.10)
                    : "transparent"
                  radius: Style.space(4)

                  MouseArea {
                    id: messageHover
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.openDetail(modelData.uid)
                  }

                  Column {
                    id: messageBody
                    anchors.left: parent.left
                    anchors.leftMargin: Style.space(14)
                    anchors.right: parent.right
                    anchors.rightMargin: Style.space(14)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(2)

                    Item {
                      width: parent.width
                      height: fromText.implicitHeight

                      Text {
                        // Mail decides what is in these strings. Without this Qt guesses
                        // whether the text is rich text and would render markup from a subject.
                        textFormat: Text.PlainText
                        id: fromText
                        anchors.left: parent.left
                        anchors.right: timeText.left
                        anchors.rightMargin: Style.space(8)
                        text: modelData.from
                        elide: Text.ElideRight
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.bold: true
                      }

                      Text {
                        // Mail decides what is in these strings. Without this Qt guesses
                        // whether the text is rich text and would render markup from a subject.
                        textFormat: Text.PlainText
                        id: timeText
                        anchors.right: parent.right
                        text: root.whenText(modelData.date)
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                      }
                    }

                    Text {
                      // Mail decides what is in these strings. Without this Qt guesses
                      // whether the text is rich text and would render markup from a subject.
                      textFormat: Text.PlainText
                      width: parent.width
                      text: modelData.subject
                      elide: Text.ElideRight
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                    }
                  }
                }
              }
            }
          }

          // ---- right, alternate: the open message and its reply box
          Item {
            id: reader
            anchors.left: divider.right
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            visible: root.detailMode
            clip: true

            // ---- back bar
            Item {
              id: readerHeader
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              height: backRow.implicitHeight + Style.space(10)

              Row {
                id: backRow
                anchors.left: parent.left
                anchors.leftMargin: Style.space(14)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(6)

                Text {
                  // Mail decides what is in these strings. Without this Qt guesses
                  // whether the text is rich text and would render markup from a subject.
                  textFormat: Text.PlainText
                  text: "←  Back"
                  color: backArea.containsMouse ? root.accent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              MouseArea {
                id: backArea
                anchors.fill: backRow
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.closeDetail()
              }

              Text {
                // Mail decides what is in these strings. Without this Qt guesses
                // whether the text is rich text and would render markup from a subject.
                textFormat: Text.PlainText
                anchors.right: parent.right
                anchors.rightMargin: Style.space(14)
                anchors.verticalCenter: parent.verticalCenter
                visible: root.detailLoading
                text: "Loading…"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
            }

            Text {
              // Mail decides what is in these strings. Without this Qt guesses
              // whether the text is rich text and would render markup from a subject.
              textFormat: Text.PlainText
              anchors.top: readerHeader.bottom
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.margins: Style.space(14)
              visible: root.detailError !== ""
              wrapMode: Text.WordWrap
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              text: root.detailError
            }

            // ---- the mail itself
            Flickable {
              id: readerScroll
              anchors.top: readerHeader.bottom
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.bottom: composer.top
              anchors.bottomMargin: Style.space(8)
              visible: root.detail !== null
              contentWidth: width
              contentHeight: readerColumn.implicitHeight
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              interactive: contentHeight > height

              Column {
                id: readerColumn
                width: readerScroll.width
                spacing: Style.space(4)

                Text {
                  // Mail decides what is in these strings. Without this Qt guesses
                  // whether the text is rich text and would render markup from a subject.
                  textFormat: Text.PlainText
                  width: parent.width - Style.space(28)
                  x: Style.space(14)
                  text: root.detail ? root.detail.subject : ""
                  wrapMode: Text.WordWrap
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }

                Text {
                  // Mail decides what is in these strings. Without this Qt guesses
                  // whether the text is rich text and would render markup from a subject.
                  textFormat: Text.PlainText
                  width: parent.width - Style.space(28)
                  x: Style.space(14)
                  text: root.detail
                    ? root.detail.from + "  ·  " + root.fullWhenText(root.detail.date)
                    : ""
                  wrapMode: Text.WordWrap
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  // Mail decides what is in these strings. Without this Qt guesses
                  // whether the text is rich text and would render markup from a subject.
                  textFormat: Text.PlainText
                  width: parent.width - Style.space(28)
                  x: Style.space(14)
                  visible: root.detail && root.detail.attachments
                    && root.detail.attachments.length > 0
                  text: root.detail && root.detail.attachments
                    ? "󰁦  " + root.detail.attachments.join(", ") : ""
                  wrapMode: Text.WordWrap
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }

                PanelSeparator { width: parent.width }

                // Selectable so a code, a link or an address can be copied out.
                TextEdit {
                  width: parent.width - Style.space(28)
                  x: Style.space(14)
                  topPadding: Style.space(6)
                  text: root.detail ? root.detail.body : ""
                  readOnly: true
                  selectByMouse: true
                  wrapMode: TextEdit.Wrap
                  color: root.foreground
                  selectionColor: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.35)
                  selectedTextColor: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  // Mail decides what is in these strings. Without this Qt guesses
                  // whether the text is rich text and would render markup from a subject.
                  textFormat: Text.PlainText
                  width: parent.width - Style.space(28)
                  x: Style.space(14)
                  visible: root.detail && root.detail.truncated === true
                  text: "… message truncated — open it in a mail client to see the rest."
                  wrapMode: Text.WordWrap
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.italic: true
                }
              }
            }

            // ---- reply composer
            Column {
              id: composer
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.bottom: parent.bottom
              anchors.leftMargin: Style.space(14)
              anchors.rightMargin: Style.space(14)
              anchors.bottomMargin: Style.space(10)
              visible: root.detail !== null
              spacing: Style.space(6)

              PanelSeparator { width: parent.width }

              Item {
                width: parent.width
                height: recipientText.implicitHeight

                Text {
                  // Mail decides what is in these strings. Without this Qt guesses
                  // whether the text is rich text and would render markup from a subject.
                  textFormat: Text.PlainText
                  id: recipientText
                  anchors.left: parent.left
                  anchors.right: replyAllToggle.left
                  anchors.rightMargin: Style.space(8)
                  text: root.replyRecipients.length > 0
                    ? "To: " + root.replyRecipients.join(", ")
                      + (root.replyCc.length > 0 ? "  ·  Cc: " + root.replyCc.join(", ") : "")
                    : "No reply address on this message."
                  elide: Text.ElideRight
                  color: root.replyRecipients.length > 0 ? root.dim : root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  // Mail decides what is in these strings. Without this Qt guesses
                  // whether the text is rich text and would render markup from a subject.
                  textFormat: Text.PlainText
                  id: replyAllToggle
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  visible: root.detail && root.detail.replyAllCc
                    && root.detail.replyAllCc.length > 0
                  text: (root.replyAll ? "󰄲  " : "󰄱  ") + "Reply all"
                  color: replyAllArea.containsMouse || root.replyAll ? root.accent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body

                  MouseArea {
                    id: replyAllArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.replyAll = !root.replyAll
                  }
                }
              }

              Rectangle {
                width: parent.width
                height: Style.space(84)
                radius: Style.cornerRadius
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.06)
                border.width: 1
                border.color: replyField.activeFocus
                  ? root.accent
                  : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.15)

                Flickable {
                  id: replyScroll
                  anchors.fill: parent
                  anchors.margins: Style.space(8)
                  contentWidth: width
                  contentHeight: replyField.implicitHeight
                  clip: true
                  boundsBehavior: Flickable.StopAtBounds
                  interactive: contentHeight > height

                  TextEdit {
                    id: replyField
                    width: replyScroll.width
                    text: root.replyBody
                    onTextChanged: root.replyBody = text
                    enabled: !root.sending
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    color: root.foreground
                    selectionColor: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.35)
                    selectedTextColor: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    // Ctrl+Return sends; a bare Return has to stay a newline,
                    // or a two-paragraph reply becomes impossible to write.
                    Keys.onPressed: function (event) {
                      if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                          && (event.modifiers & Qt.ControlModifier)) {
                        root.sendReply()
                        event.accepted = true
                      }
                    }

                    // Keep the caret in view while typing past the bottom.
                    onCursorRectangleChanged: replyScroll.contentY = Math.max(
                      0, Math.min(cursorRectangle.y + cursorRectangle.height - replyScroll.height,
                                  replyScroll.contentHeight - replyScroll.height))
                  }
                }

                Text {
                  // Mail decides what is in these strings. Without this Qt guesses
                  // whether the text is rich text and would render markup from a subject.
                  textFormat: Text.PlainText
                  anchors.left: parent.left
                  anchors.top: parent.top
                  anchors.margins: Style.space(8)
                  visible: root.replyBody === "" && !replyField.activeFocus
                  text: "Write a reply…  (Ctrl+Enter sends)"
                  color: Qt.darker(root.foreground, 1.9)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
              }

              Text {
                // Mail decides what is in these strings. Without this Qt guesses
                // whether the text is rich text and would render markup from a subject.
                textFormat: Text.PlainText
                width: parent.width
                visible: root.service && (root.service.sendError !== ""
                  || root.service.sendWarning !== "")
                wrapMode: Text.WordWrap
                text: root.service
                  ? (root.service.sendError !== "" ? root.service.sendError
                                                   : root.service.sendWarning) : ""
                color: root.service && root.service.sendError !== "" ? root.urgent : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }

              Item {
                width: parent.width
                height: sendButton.implicitHeight

                Row {
                  anchors.right: parent.right
                  spacing: Style.space(8)

                  Button {
                    id: markButton
                    text: "Mark read"
                    enabled: !root.sending
                    onClicked: root.markRead()
                  }

                  Button {
                    id: sendButton
                    text: root.sending ? "Sending…" : "Send"
                    enabled: root.canSend
                    onClicked: root.sendReply()
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
