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
