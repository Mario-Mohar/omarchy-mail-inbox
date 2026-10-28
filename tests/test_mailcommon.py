"""Tests for the shared plumbing behind the mail-inbox helpers.

Two themes run through this module and through these tests. The first is that
accounts.json holds credentials-adjacent configuration, so reading it refuses a
symlink, a file somebody else owns, and one other users can read. The second is
that everything crossing into the shell is bounded: the consumer is a QML
StdioCollector that buffers a whole response before anything inspects it, so a
mailbox with absurd headers must not be able to grow that buffer without limit.
"""

import json
import os

import mailcommon
import pytest


@pytest.fixture
def accounts_file(tmp_path, monkeypatch):
    """Point the module at a private accounts.json inside tmp_path."""
    path = tmp_path / "accounts.json"
    monkeypatch.setattr(mailcommon, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(mailcommon, "ACCOUNTS_FILE", str(path))
    return path


def write_private(path, text):
    path.write_text(text)
    os.chmod(path, 0o600)


# --------------------------------------------------------------------------
# reading accounts.json
# --------------------------------------------------------------------------

def test_missing_file_is_no_accounts(accounts_file):
    assert mailcommon.load_accounts() == []


def test_reads_a_list_of_accounts(accounts_file):
    write_private(accounts_file, json.dumps({"accounts": [{"id": "work"}, {"id": "home"}]}))
    assert [a["id"] for a in mailcommon.load_accounts()] == ["work", "home"]


def test_accepts_a_bare_list_as_well_as_a_wrapper(accounts_file):
    write_private(accounts_file, json.dumps([{"id": "work"}]))
    assert [a["id"] for a in mailcommon.load_accounts()] == ["work"]


def test_refuses_a_symlink(accounts_file, tmp_path):
    real = tmp_path / "elsewhere.json"
    write_private(real, "[]")
    os.symlink(real, accounts_file)

    with pytest.raises(mailcommon.AccountError, match="symlink"):
        mailcommon.load_accounts()


def test_refuses_a_file_others_can_read(accounts_file):
    accounts_file.write_text("[]")
    os.chmod(accounts_file, 0o644)

    with pytest.raises(mailcommon.AccountError, match="readable by others"):
        mailcommon.load_accounts()


def test_refuses_a_directory(accounts_file):
    accounts_file.mkdir()
    with pytest.raises(mailcommon.AccountError, match="not a regular file"):
        mailcommon.load_accounts()


def test_refuses_a_file_over_the_cap(accounts_file, monkeypatch):
    monkeypatch.setattr(mailcommon, "MAX_CONFIG_BYTES", 64)
    write_private(accounts_file, json.dumps([{"id": "x" * 200}]))

    with pytest.raises(mailcommon.AccountError, match="larger than"):
        mailcommon.load_accounts()


def test_reports_unreadable_json_rather_than_raising_valueerror(accounts_file):
    write_private(accounts_file, "{not json")
    with pytest.raises(mailcommon.AccountError, match="unreadable"):
        mailcommon.load_accounts()


def test_a_json_document_of_the_wrong_shape_is_no_accounts(accounts_file):
    write_private(accounts_file, json.dumps({"accounts": "not a list"}))
    assert mailcommon.load_accounts() == []


# --------------------------------------------------------------------------
# bounding what comes out of the config
# --------------------------------------------------------------------------

def test_the_account_list_is_capped(accounts_file, monkeypatch):
    monkeypatch.setattr(mailcommon, "MAX_ACCOUNTS", 3)
    write_private(accounts_file, json.dumps([{"id": str(n)} for n in range(20)]))
    assert len(mailcommon.load_accounts()) == 3


def test_field_values_are_clipped(accounts_file, monkeypatch):
    monkeypatch.setattr(mailcommon, "MAX_FIELD_VALUE", 10)
    write_private(accounts_file, json.dumps([{"id": "x" * 100}]))
    assert mailcommon.load_accounts()[0]["id"] == "x" * 10


def test_field_count_per_account_is_capped(accounts_file, monkeypatch):
    monkeypatch.setattr(mailcommon, "MAX_FIELDS_PER_ACCOUNT", 4)
    entry = {"k%02d" % n: n for n in range(30)}
    write_private(accounts_file, json.dumps([entry]))
    assert len(mailcommon.load_accounts()[0]) == 4


def test_non_dict_entries_are_dropped(accounts_file):
    write_private(accounts_file, json.dumps([{"id": "ok"}, "nonsense", 42, None]))
    assert [a["id"] for a in mailcommon.load_accounts()] == ["ok"]


def test_clip_value_leaves_scalars_alone():
    assert mailcommon._clip_value(7) == 7
    assert mailcommon._clip_value(1.5) == 1.5
    assert mailcommon._clip_value(True) is True
    assert mailcommon._clip_value(None) is None


def test_clip_value_stringifies_anything_else():
    assert mailcommon._clip_value({"a": 1}) == "{'a': 1}"


# --------------------------------------------------------------------------
# find_account
# --------------------------------------------------------------------------

def test_find_account_matches_on_id(accounts_file):
    write_private(accounts_file, json.dumps([{"id": "work"}, {"id": "home"}]))
    assert mailcommon.find_account("home")["id"] == "home"


def test_find_account_is_none_when_absent(accounts_file):
    write_private(accounts_file, json.dumps([{"id": "work"}]))
    assert mailcommon.find_account("nope") is None


def test_find_account_compares_as_text(accounts_file):
    # A numeric id in the file must still be findable by its string form.
    write_private(accounts_file, json.dumps([{"id": 7}]))
    assert mailcommon.find_account("7") is not None


def test_require_account_raises_for_an_unknown_id(accounts_file):
    write_private(accounts_file, json.dumps([{"id": "work"}]))
    with pytest.raises(mailcommon.AccountError):
        mailcommon.require_account("nope")


# --------------------------------------------------------------------------
# header decoding
# --------------------------------------------------------------------------

def test_decode_field_decodes_an_encoded_word():
    assert mailcommon.decode_field("=?utf-8?q?Gr=C3=BC=C3=9Fe?=") == "Grüße"


def test_decode_field_collapses_folded_whitespace():
    assert mailcommon.decode_field("a\r\n\tlong   subject") == "a long subject"


def test_decode_field_is_empty_for_a_missing_header():
    assert mailcommon.decode_field(None) == ""
    assert mailcommon.decode_field("") == ""


def test_decode_field_clips_to_its_limit():
    assert mailcommon.decode_field("x" * 500, limit=10) == "x" * 10


def test_decode_field_survives_an_unknown_charset():
    # A header naming a charset Python does not have must not raise.
    assert mailcommon.decode_field("=?nosuchcharset?q?hi?=")


def test_sender_name_prefers_the_display_name():
    assert mailcommon.sender_name("Ada Lovelace <ada@example.com>") == "Ada Lovelace"


def test_sender_name_falls_back_to_the_address():
    assert mailcommon.sender_name("ada@example.com") == "ada@example.com"


def test_sender_name_has_a_last_resort():
    assert mailcommon.sender_name("") == "(unknown)"


def test_iso_date_parses_an_rfc_2822_date():
    assert mailcommon.iso_date("Mon, 31 Aug 2026 10:00:00 +0200").startswith("2026-08-31")


def test_iso_date_is_empty_for_nonsense():
    assert mailcommon.iso_date("not a date") == ""
    assert mailcommon.iso_date(None) == ""


# --------------------------------------------------------------------------
# bounding what goes out to the shell
# --------------------------------------------------------------------------

def test_bound_clips_strings(monkeypatch):
    monkeypatch.setattr(mailcommon, "MAX_FIELD_VALUE", 5)
    assert mailcommon._bound("x" * 50) == "x" * 5


def test_bound_clips_lists(monkeypatch):
    monkeypatch.setattr(mailcommon, "MAX_ACCOUNTS", 2)
    assert mailcommon._bound(list(range(10))) == [0, 1]


def test_bound_clips_dict_size_and_keys(monkeypatch):
    monkeypatch.setattr(mailcommon, "MAX_FIELDS_PER_ACCOUNT", 2)
    assert len(mailcommon._bound({"a": 1, "b": 2, "c": 3})) == 2


def test_bound_stops_at_its_depth_limit():
    deep = current = {}
    for _ in range(20):
        current["next"] = {}
        current = current["next"]
    assert mailcommon._bound(deep) is not None  # it returns, rather than recursing away


def test_bound_leaves_scalars_alone():
    assert mailcommon._bound(7) == 7
    assert mailcommon._bound(None) is None


def test_emit_writes_one_json_line(capsys):
    mailcommon.emit({"ok": True})
    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert json.loads(out) == {"ok": True}


def test_emit_keeps_non_ascii_readable(capsys):
    mailcommon.emit({"subject": "Grüße"})
    assert "Grüße" in capsys.readouterr().out


def test_emit_refuses_a_document_over_the_cap(monkeypatch, capsys):
    monkeypatch.setattr(mailcommon, "MAX_EMIT_BYTES", 32)
    monkeypatch.setattr(mailcommon, "MAX_FIELD_VALUE", 10_000)
    monkeypatch.setattr(mailcommon, "MAX_ACCOUNTS", 10_000)
    mailcommon.emit({"body": "x" * 5000})
    assert json.loads(capsys.readouterr().out) == {
        "error": "response too large, refusing to emit"
    }


def _fake_imap_server(literal_size, body_chunks):
    """A one-shot IMAP server that answers any FETCH with one literal.

    It announces `literal_size` bytes and then sends `body_chunks` chunks of
    64 KB, recording how much it got out before the client hung up.
    """
    import socket
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    sent = []

    def serve():
        conn, _ = listener.accept()
        reader = conn.makefile("rb")
        try:
            conn.sendall(b"* OK ready\r\n")
            while True:
                line = reader.readline()
                if not line:
                    break
                tag, command = line.rstrip(b"\r\n").split(b" ", 2)[:2]
                if command.upper() == b"CAPABILITY":
                    conn.sendall(b"* CAPABILITY IMAP4rev1\r\n" + tag + b" OK done\r\n")
                    continue
                conn.sendall(b"* 1 FETCH (UID 1 BODY[HEADER] {%d}\r\n" % literal_size)
                chunk = b"x" * 65536
                for _ in range(body_chunks):
                    conn.sendall(chunk)
                    sent.append(len(chunk))
                conn.sendall(b")\r\n" + tag + b" OK done\r\n")
        except OSError:
            pass
        finally:
            conn.close()
            listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], sent


def _hang_up(conn):
    # The client may already have dropped the connection on overflow.
    try:
        conn.shutdown()
    except OSError:
        pass


def _connect(port):
    conn = mailcommon.BoundedIMAP4("127.0.0.1", port)
    conn.state = "SELECTED"  # skip login and select, the server does not care
    return conn


def test_an_oversized_literal_is_refused_before_it_is_read():
    import imaplib

    size = 1024 * 1024 * 1024
    port, sent = _fake_imap_server(size, size // 65536)
    conn = _connect(port)
    try:
        with pytest.raises(imaplib.IMAP4.abort):
            conn.uid("FETCH", "1", "(BODY.PEEK[HEADER])")
    finally:
        _hang_up(conn)
    # Only what the socket buffers took before the client gave up.
    assert sum(sent) < 64 * 1024 * 1024


def test_a_literal_within_the_budget_is_read():
    port, _ = _fake_imap_server(4 * 65536, 4)
    conn = _connect(port)
    try:
        status, payload = conn.uid("FETCH", "1", "(BODY.PEEK[HEADER])")
    finally:
        _hang_up(conn)
    assert status == "OK"
    assert len(payload[0][1]) == 4 * 65536


def test_the_response_budget_is_per_command():
    port, _ = _fake_imap_server(4 * 65536, 4)
    conn = _connect(port)
    conn.response_budget = 5 * 65536
    try:
        for _ in range(3):
            status, _payload = conn.uid("FETCH", "1", "(BODY.PEEK[HEADER])")
            assert status == "OK"
    finally:
        _hang_up(conn)


def _flooding_imap_server(line_count):
    """Answers any command with `line_count` untagged OK lines and no completion."""
    import socket
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    sent = []

    def serve():
        conn, _ = listener.accept()
        reader = conn.makefile("rb")
        try:
            conn.sendall(b"* OK ready\r\n")
            while True:
                line = reader.readline()
                if not line:
                    break
                tag, command = line.rstrip(b"\r\n").split(b" ", 2)[:2]
                if command.upper() == b"CAPABILITY":
                    conn.sendall(b"* CAPABILITY IMAP4rev1\r\n" + tag + b" OK done\r\n")
                    continue
                filler = b"* OK " + b"x" * 1000 + b"\r\n"
                for _ in range(line_count):
                    conn.sendall(filler)
                    sent.append(len(filler))
        except OSError:
            pass
        finally:
            conn.close()
            listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], sent


def test_endless_untagged_lines_are_cut_off():
    """Each line is valid and short; their sum must still be bounded."""
    import imaplib

    port, sent = _flooding_imap_server(1_000_000)
    conn = _connect(port)
    try:
        with pytest.raises(imaplib.IMAP4.abort):
            conn.uid("FETCH", "1", "(BODY.PEEK[HEADER])")
    finally:
        _hang_up(conn)
    held = sum(len(line) for line in conn.untagged_responses.get("OK", []))
    assert held <= mailcommon.MAX_RESPONSE_BYTES
    # Only what the socket buffers took before the client hung up.
    assert sum(sent) < 64 * 1024 * 1024
