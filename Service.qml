import QtQuick
import Quickshell
import Quickshell.Io

// Polls every configured mailbox by shelling out to bin/mail-check --all,
// which does the IMAP work in parallel and prints one JSON blob. Kept
// deliberately dumb: the widget never sees a password, and a failing poll
// leaves the last good numbers on screen.
//
// Opening, flagging and answering a message go through their own one-shot
// helpers. Everything addresses mail by UID plus the mailbox's UIDVALIDITY,
// so a poll that lands between "click" and "send" cannot redirect an action
// onto a different message.
Item {
  id: root

  property var settings: ({})

  // One entry per mailbox: {id, label, unread, messages, uidvalidity, error}
  property var accounts: []
  property int totalUnread: 0
  property string error: ""
  property bool everLoaded: false
  property var generatedAt: null
  readonly property bool polling: pollProcess.running

  // ---- open message state, owned here so the panel stays declarative
  property var detail: null            // parsed mail-read payload
  property string detailKey: ""        // "<account>/<uid>" currently requested
  property string detailError: ""
  readonly property bool detailLoading: readProcess.running

  property bool sending: false
  property string sendError: ""
  property string sendWarning: ""
  signal replySent()

  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 300, 60, 3600)
  readonly property int maxMessages: intSetting("maxMessages", 12, 1, 50)
  readonly property bool configured: accounts.length > 0

  readonly property int erroredCount: {
    var n = 0
    for (var i = 0; i < accounts.length; i++)
      if (accounts[i].error && accounts[i].error !== "") n++
    return n
  }

  function intSetting(name, fallback, min, max) {
    var v = parseInt(settings ? settings[name] : undefined, 10)
    if (isNaN(v)) return fallback
    return Math.max(min, Math.min(max, v))
  }

  function boolSetting(name, fallback) {
    var v = settings ? settings[name] : undefined
    if (v === undefined || v === null) return fallback
    return v === true || String(v).toLowerCase() === "true" || String(v) === "1"
  }

  function scriptPath(name) {
    return String(Qt.resolvedUrl("bin/" + name)).replace(/^file:\/\//, "")
  }

  function refresh() {
    if (pollProcess.running) return
    pollProcess.command = [scriptPath("mail-check"), "--all",
                           "--limit", String(maxMessages)]
    pollProcess.running = true
  }

  function accountAt(index) {
    return index >= 0 && index < accounts.length ? accounts[index] : null
  }

  function indexOfId(id) {
    for (var i = 0; i < accounts.length; i++)
      if (accounts[i].id === id) return i
    return -1
  }

  function accountById(id) {
    var i = indexOfId(id)
    return i >= 0 ? accounts[i] : null
  }

  function uidValidityOf(accountId) {
    var a = accountById(accountId)
    return a && a.uidvalidity ? String(a.uidvalidity) : ""
  }

  // Opens the account setup helper in the user's terminal.
  function openSetup() {
    Quickshell.execDetached(["omarchy-launch-terminal", scriptPath("mail-inbox-setup")])
  }

  // ------------------------------------------------------------- reading
  function openMessage(accountId, uid) {
    if (!accountId || !uid) return
    if (readProcess.running) return
    root.detail = null
    root.detailError = ""
    root.sendError = ""
    root.sendWarning = ""
    root.detailKey = accountId + "/" + uid
    readProcess.command = [scriptPath("mail-read"),
                           "--account", String(accountId),
                           "--uid", String(uid),
                           "--uidvalidity", uidValidityOf(accountId)]
    readProcess.running = true
  }

  function closeMessage() {
    root.detail = null
    root.detailKey = ""
    root.detailError = ""
    root.sendError = ""
    root.sendWarning = ""
  }

  // ------------------------------------------------------------ flagging
  function markSeen(accountId, uid) {
    if (!accountId || !uid || markProcess.running) return
    markProcess.command = [scriptPath("mail-mark"),
                           "--account", String(accountId),
                           "--uid", String(uid),
                           "--flag", "seen",
                           "--uidvalidity", uidValidityOf(accountId)]
    markProcess.running = true
  }

  // ------------------------------------------------------------- sending
  // request: {account, to[], cc[], subject, body, inReplyTo, references, uid}
  function sendReply(request) {
    if (root.sending || !request) return
    root.sendError = ""
    root.sendWarning = ""
    root.sending = true
    sendProcess.payload = JSON.stringify(request)
    sendProcess.command = [scriptPath("mail-send")]
    sendProcess.running = true
  }

  function apply(text) {
    var payload
    try {
      payload = JSON.parse(text)
    } catch (e) {
      root.error = "backend returned unparseable output"
      root.everLoaded = true
      return
    }
    root.everLoaded = true
    root.generatedAt = new Date()
    root.error = payload.error ? String(payload.error) : ""
    if (Array.isArray(payload.accounts)) {
      root.accounts = payload.accounts
      root.totalUnread = parseInt(payload.totalUnread, 10) || 0
    }
  }

  // A StdioCollector buffers everything the helper writes before anything gets
  // to look at it, so a helper that goes wrong could grow that buffer without
  // limit. The helpers cap what they emit; this is the second half of the same
  // rule, on the side that does the buffering. Watchdogs cover the other
  // failure: a helper that never exits at all.
  readonly property int maxStdoutChars: 512 * 1024
  readonly property int watchdogMs: 90000

  function takeOutput(collector, what) {
    var text = String(collector.text || "")
    if (text.length > root.maxStdoutChars) {
      console.warn("mail-inbox: " + what + " produced "
                   + text.length + " characters, discarding")
      return ""
    }
    return text
  }

  Timer {
    id: pollWatchdog
    interval: root.watchdogMs
    onTriggered: { pollProcess.signal(15); root.error = "mail-check timed out" }
  }

  Process {
    id: pollProcess
    command: []
    stdout: StdioCollector { id: pollStdout; waitForEnd: true }
    onRunningChanged: running ? pollWatchdog.restart() : pollWatchdog.stop()
    onExited: function (exitCode) {
      if (exitCode === 0) root.apply(root.takeOutput(pollStdout, "mail-check"))
      else {
        root.error = "mail-check failed (exit " + exitCode + ")"
        root.everLoaded = true
      }
    }
  }

  Timer {
    id: readWatchdog
    interval: root.watchdogMs
    onTriggered: { readProcess.signal(15); root.detailError = "mail-read timed out" }
  }

  Process {
    id: readProcess
    command: []
    stdout: StdioCollector { id: readStdout; waitForEnd: true }
    onRunningChanged: running ? readWatchdog.restart() : readWatchdog.stop()
    onExited: function (exitCode) {
      if (exitCode !== 0) {
        root.detailError = "mail-read failed (exit " + exitCode + ")"
        return
      }
      var payload
      try {
        payload = JSON.parse(root.takeOutput(readStdout, "mail-read"))
      } catch (e) {
        root.detailError = "could not read that message"
        return
      }
      // A stale reply must not overwrite a newer request.
      if (root.detailKey !== payload.account + "/" + payload.uid) return
      if (payload.error && payload.error !== "") {
        root.detailError = String(payload.error)
        return
      }
      root.detail = payload
    }
  }

  Process {
    id: markProcess
    command: []
    stdout: StdioCollector { id: markStdout; waitForEnd: true }
    onExited: function (exitCode) {
      // The unread counts are now wrong either way; let the poll settle it.
      root.refresh()
    }
  }

  Timer {
    id: sendWatchdog
    interval: root.watchdogMs
    onTriggered: {
      sendProcess.signal(15)
      root.sending = false
      root.sendError = "mail-send timed out"
    }
  }

  Process {
    id: sendProcess
    property string payload: ""
    command: []
    stdinEnabled: true
    stdout: StdioCollector { id: sendStdout; waitForEnd: true }
    onRunningChanged: running ? sendWatchdog.restart() : sendWatchdog.stop()
    onStarted: {
      write(payload)
      payload = ""
      stdinEnabled = false   // closing stdin is what tells mail-send to start
    }
    onExited: function (exitCode) {
      root.sending = false
      if (exitCode !== 0) {
        root.sendError = "mail-send failed (exit " + exitCode + ")"
        return
      }
      var result
      try {
        result = JSON.parse(root.takeOutput(sendStdout, "mail-send"))
      } catch (e) {
        root.sendError = "mail-send returned unparseable output"
        return
      }
      if (!result.sent) {
        root.sendError = result.error ? String(result.error) : "the reply was not sent"
        return
      }
      root.sendWarning = result.warning ? String(result.warning) : ""
      root.replySent()
      root.refresh()
    }
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    onTriggered: root.refresh()
  }

  Timer {
    interval: 1500
    repeat: false
    running: true
    onTriggered: root.refresh()
  }

  // accounts.json is edited by mail-inbox-account, so pick up new mailboxes
  // without waiting for the next interval.
  FileView {
    path: Quickshell.env("HOME") + "/.config/omarchy/mail-inbox"
    watchChanges: true
    printErrors: false
    onFileChanged: root.refresh()
  }
}
