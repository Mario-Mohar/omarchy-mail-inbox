"""Static checks on the QML side.

The widget only runs inside a live Quickshell, which the tests do not have.
These pin down the few decisions in the QML that a bug report was about, by
reading the source rather than running it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def source(name):
    return (ROOT / name).read_text(encoding="utf-8")


def function_body(text, name):
    """The text of `function name(...) { ... }`, braces balanced."""
    match = re.search(r"function %s\s*\([^)]*\)\s*\{" % re.escape(name), text)
    assert match, "function %s not found" % name
    depth, i = 1, match.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[match.end():i - 1]


# ---- #16: a failing mailbox keeps its last counts ----------------------------------

def test_apply_does_not_take_failed_counts_wholesale():
    body = function_body(source("Service.qml"), "apply")
    assert "root.accounts = payload.accounts" not in body
    assert "keepLastCounts(payload.accounts)" in body
    # The total follows the merged list, not the backend's sum.
    assert "payload.totalUnread" not in body


def test_failed_mailbox_carries_over_unread_and_messages():
    body = function_body(source("Service.qml"), "keepLastCounts")
    assert "entry.error" in body
    for field in ("unread", "messages", "uidvalidity"):
        assert '"%s": last.%s' % (field, field) in body
