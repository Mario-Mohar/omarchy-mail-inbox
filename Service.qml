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

  // Saving or opening an attachment, or the HTML version. One at a time; the
  // message line under the attachments says what happened.
  property string partBusy: ""         // "" or a short label of what runs
  property string partMessage: ""
  property bool partFailed: false

  property bool sending: false
  property string sendError: ""
  property string sendWarning: ""
  signal replySent()

  // Reply drafts through the local Claude CLI. Off unless the user turns the
  // setting on, because the open message is sent to Anthropic for it.
  readonly property bool aiDraftEnabled: boolSetting("aiDraft", false)
  readonly property string aiDraftModel: {
    var m = String(settings && settings.aiDraftModel ? settings.aiDraftModel : "sonnet")
    return ["sonnet", "haiku", "opus"].indexOf(m) >= 0 ? m : "sonnet"
  }
  readonly property string aiDraftSignature: String(settings && settings.aiDraftSignature
                                                    ? settings.aiDraftSignature : "")
  property bool aiDraftAvailable: false     // claude found by mail-draft --check
  readonly property bool drafting: draftProcess.running
  property string draftError: ""
  property string draftKey: ""              // detailKey the running draft belongs to
  signal draftReady(string text)

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
    root.partMessage = ""
    root.partFailed = false
    cancelDraft()
    root.detailKey = accountId + "/" + uid
    readProcess.command = [scriptPath("mail-read"),
                           "--account", String(accountId),
                           "--uid", String(uid),
                           "--uidvalidity", uidValidityOf(accountId)]
    readProcess.running = true
  }

  function closeMessage() {
    cancelDraft()
    root.partMessage = ""
    root.partFailed = false
    root.detail = null
    root.detailKey = ""
    root.detailError = ""
    root.sendError = ""
    root.sendWarning = ""
  }

  // --------------------------------------------------------- attachments
  // index is the position in detail.attachmentParts, which mail-attachment
  // rebuilds from the same BODYSTRUCTURE.
  function fetchAttachment(index, open) {
    if (!root.detail || partProcess.running) return
    var parts = root.detail.attachmentParts || []
    if (index < 0 || index >= parts.length) return
    var cmd = [scriptPath("mail-attachment"),
               "--account", String(root.detail.account),
               "--uid", String(root.detail.uid),
               "--uidvalidity", uidValidityOf(root.detail.account),
               "--index", String(index)]
    if (open) cmd.push("--open")
    root.partBusy = (open ? "Opening " : "Saving ") + String(parts[index].name || "")
    root.partMessage = ""
    root.partFailed = false
    partProcess.command = cmd
    partProcess.running = true
  }

  function openHtml() {
    if (!root.detail || partProcess.running) return
    root.partBusy = "Opening in the browser"
    root.partMessage = ""
    root.partFailed = false
    partProcess.command = [scriptPath("mail-html"),
                           "--account", String(root.detail.account),
                           "--uid", String(root.detail.uid),
                           "--uidvalidity", uidValidityOf(root.detail.account)]
    partProcess.running = true
  }

  function homeShort(path) {
    var home = Quickshell.env("HOME")
    return home && path.indexOf(home + "/") === 0 ? "~" + path.slice(home.length) : path
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

  // -------------------------------------------------------------- drafts
  // notes: whatever is already in the reply box, used as instructions.
  function draftReply(notes) {
    if (!root.detail || !root.aiDraftEnabled || draftProcess.running) return
    root.draftError = ""
    root.draftKey = root.detailKey
    draftProcess.payload = JSON.stringify({
      "from": root.detail.from || "",
      "subject": root.detail.subject || "",
      "date": root.detail.date || "",
      "body": root.detail.body || "",
      "notes": String(notes || ""),
      "signature": root.aiDraftSignature,
      "model": root.aiDraftModel
    })
    draftProcess.command = [scriptPath("mail-draft")]
    draftProcess.running = true
  }

  function cancelDraft() {
    root.draftKey = ""
    root.draftError = ""
    if (draftProcess.running) draftProcess.signal(15)
  }

  function checkDraftAvailable() {
    if (!root.aiDraftEnabled || checkDraftProcess.running) return
    checkDraftProcess.command = [scriptPath("mail-draft"), "--check"]
    checkDraftProcess.running = true
  }

  onAiDraftEnabledChanged: checkDraftAvailable()
  Component.onCompleted: checkDraftAvailable()

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

  Timer {
    id: partWatchdog
    interval: root.watchdogMs
    onTriggered: {
      partProcess.signal(15)
      root.partBusy = ""
      root.partFailed = true
      root.partMessage = "timed out"
    }
  }

  Process {
    id: partProcess
    command: []
    stdout: StdioCollector { id: partStdout; waitForEnd: true }
    onRunningChanged: running ? partWatchdog.restart() : partWatchdog.stop()
    onExited: function (exitCode) {
      root.partBusy = ""
      var result
      try {
        result = JSON.parse(root.takeOutput(partStdout, "attachment helper"))
      } catch (e) {
        result = { "error": "failed (exit " + exitCode + ")" }
      }
      if (result.error) {
        root.partFailed = true
        root.partMessage = String(result.error)
      } else if (result.saved) {
        root.partFailed = false
        root.partMessage = "Saved to " + root.homeShort(String(result.path || ""))
      } else {
        root.partFailed = false
        root.partMessage = ""
      }
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

  Process {
    id: checkDraftProcess
    command: []
    stdout: StdioCollector { id: checkDraftStdout; waitForEnd: true }
    onExited: function (exitCode) {
      try {
        root.aiDraftAvailable = exitCode === 0
          && JSON.parse(root.takeOutput(checkDraftStdout, "mail-draft")).available === true
      } catch (e) {
        root.aiDraftAvailable = false
      }
    }
  }

  Timer {
    id: draftWatchdog
    interval: root.watchdogMs
    onTriggered: {
      draftProcess.signal(15)
      root.draftError = "mail-draft timed out"
    }
  }

  Process {
    id: draftProcess
    property string payload: ""
    command: []
    stdinEnabled: true
    stdout: StdioCollector { id: draftStdout; waitForEnd: true }
    onRunningChanged: running ? draftWatchdog.restart() : draftWatchdog.stop()
    onStarted: {
      write(payload)
      payload = ""
      stdinEnabled = false
    }
    onExited: function (exitCode) {
      stdinEnabled = true
      // Cancelled, or the user has moved on to another message: the draft
      // must not land in a reply to something else.
      if (root.draftKey === "" || root.draftKey !== root.detailKey) return
      root.draftKey = ""
      if (exitCode !== 0) {
        root.draftError = "mail-draft failed (exit " + exitCode + ")"
        return
      }
      var result
      try {
        result = JSON.parse(root.takeOutput(draftStdout, "mail-draft"))
      } catch (e) {
        root.draftError = "mail-draft returned unparseable output"
        return
      }
      if (result.error || !result.draft) {
        root.draftError = result.error ? String(result.error) : "no draft came back"
        return
      }
      root.draftReady(String(result.draft))
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
      // Reopen stdin for the next reply; left closed, the second send of a
      // session would hand mail-send an empty request.
      stdinEnabled = true
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
