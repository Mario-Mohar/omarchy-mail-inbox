"""Tests for attachments and the HTML part, without a mailbox.

The BODYSTRUCTURE answers below have the shape imaplib hands back: a bytes line,
or a (prefix, literal) tuple when the server sent a string as a literal. The
fake connection replays a FETCH answer, so the size limits can be checked
without a server.
"""

import base64
import importlib.machinery
import importlib.util
import os
import stat
from pathlib import Path

import mailparts
import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"

SIMPLE = [b'1 (UID 7 BODYSTRUCTURE (("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 12 1 NIL NIL NIL)'
          b'("application" "pdf" ("name" "a.pdf") NIL NIL "base64" 400 NIL ("attachment" ("filename" "a.pdf")) NIL)'
          b' "mixed" ("boundary" "x") NIL NIL))']

NESTED = [b'1 (UID 9 BODYSTRUCTURE (((("text" "plain" ("charset" "utf-8") NIL NIL "qp" 10 1 NIL NIL NIL)'
          b'("text" "html" ("charset" "iso-8859-1") NIL NIL "quoted-printable" 20 1 NIL NIL NIL) "alternative" NIL NIL NIL)'
          b'("image" "png" ("name" "logo.png") "<cid>" NIL "base64" 300 NIL ("inline" ("filename" "logo.png")) NIL) "related" NIL NIL NIL)'
          b'("application" "pdf" NIL NIL NIL "base64" 900 NIL ("attachment" ("filename*" "utf-8\'\'R%C3%BCck.pdf")) NIL) "mixed" NIL NIL NIL))']

LITERAL = [(b'1 (UID 3 BODYSTRUCTURE (("text" "plain" NIL NIL NIL "7bit" 1 1 NIL NIL NIL)'
            b'("application" "octet-stream" NIL NIL NIL "base64" 8 NIL ("attachment" ("filename" {9}',
            b'odd "name'),
           b')) NIL) "mixed" NIL NIL NIL))']


def parts_of(payload):
    return mailparts.leaves(mailparts.parse_bodystructure(payload))


# ---- structure ---------------------------------------------------------------

def test_sections_and_attachments():
    parts = parts_of(SIMPLE)
    assert [p["section"] for p in parts] == ["1", "2"]
    found = mailparts.attachments(parts)
    assert len(found) == 1
    assert found[0]["name"] == "a.pdf" and found[0]["size"] == 400 and found[0]["encoding"] == "base64"


def test_nested_sections_rfc2231_and_html():
    parts = parts_of(NESTED)
    assert [p["section"] for p in parts] == ["1.1.1", "1.1.2", "1.2", "2"]
    assert [p["name"] for p in mailparts.attachments(parts)] == ["logo.png", "Rück.pdf"]
    html = mailparts.first_html(parts)
    assert html["section"] == "1.1.2" and html["charset"] == "iso-8859-1"


def test_literal_strings():
    found = mailparts.attachments(parts_of(LITERAL))
    assert found[0]["name"] == 'odd "name'


def test_single_part_message_is_section_one():
    parts = parts_of([b'1 (UID 1 BODYSTRUCTURE ("text" "html" NIL NIL NIL "7bit" 5 1 NIL NIL NIL))'])
    assert parts[0]["section"] == "1"
    assert mailparts.first_html(parts) is parts[0]


def test_missing_structure():
    assert mailparts.parse_bodystructure([b"1 (UID 1 FLAGS ())"]) is None


def test_part_count_is_bounded():
    many = b"".join(b'("text" "plain" NIL NIL NIL "7bit" 1 1 NIL NIL NIL)' for _ in range(500))
    parts = parts_of([b"1 (UID 1 BODYSTRUCTURE (" + many + b' "mixed" NIL NIL NIL))'])
    assert len(parts) == mailparts.MAX_PARTS


def test_deep_nesting_is_refused():
    with pytest.raises(mailparts.PartError):
        parts_of([b"1 (UID 1 BODYSTRUCTURE " + b"(" * 200 + b")" * 200 + b")"])


# ---- file names --------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("../../.bashrc", "bashrc"),
    ("C:\\Users\\x\\evil.exe", "evil.exe"),
    ("a\x00b\x1f.pdf", "ab.pdf"),
    ("invoice\u202egpj.exe", "invoicegpj.exe"),
    ("  ..  ", "attachment"),
    ("", "attachment"),
    ("report.  ", "report"),
    ("Rücklastschreiben.pdf", "Rücklastschreiben.pdf"),
])
def test_sanitise_filename(raw, expected):
    assert mailparts.sanitise_filename(raw) == expected


def test_long_name_keeps_extension():
    name = mailparts.sanitise_filename("ü" * 200 + ".pdf")
    assert name.endswith(".pdf")
    assert len(name.encode("utf-8")) <= mailparts.MAX_NAME_BYTES


# ---- writing -----------------------------------------------------------------

def test_write_new_never_overwrites(tmp_path):
    first = mailparts.write_new(str(tmp_path), "a.pdf", b"1")
    second = mailparts.write_new(str(tmp_path), "a.pdf", b"2")
    assert os.path.basename(second) == "a (2).pdf"
    assert Path(first).read_bytes() == b"1"
    assert stat.S_IMODE(os.stat(second).st_mode) == 0o600


def test_write_new_refuses_planted_symlink(tmp_path):
    target = tmp_path / "victim"
    target.write_text("keep")
    (tmp_path / "a.pdf").symlink_to(target)
    path = mailparts.write_new(str(tmp_path), "a.pdf", b"x")
    assert os.path.basename(path) == "a (2).pdf"
    assert target.read_text() == "keep"


def test_private_dir_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = mailparts.private_dir()
    base = tmp_path / mailparts.PRIVATE_DIR_NAME
    assert stat.S_IMODE(os.stat(base).st_mode) == 0o700
    assert Path(path).parent == base


def test_private_dir_refuses_a_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / mailparts.PRIVATE_DIR_NAME).symlink_to(tmp_path / "elsewhere")
    with pytest.raises(mailparts.PartError):
        mailparts.private_dir()


# ---- fetching and limits -----------------------------------------------------

class FakeConn:
    def __init__(self, raw):
        self.raw = raw
        self.asked = None

    def uid(self, command, uid, spec):
        self.asked = spec
        return "OK", [(b"1 (UID 7 BODY[2]<0> {%d}" % len(self.raw), self.raw), b")"]


def part(size, encoding="base64", section="2", name="a.bin"):
    return {"section": section, "encoding": encoding, "size": size, "name": name}


def test_fetch_decodes_and_asks_for_one_section():
    data = b"hello attachment"
    conn = FakeConn(base64.encodebytes(data))
    assert mailparts.fetch_section(conn, 7, part(30), 1000) == data
    assert conn.asked.startswith("(BODY.PEEK[2]<0.")


def test_announced_size_over_limit_is_refused_before_fetching():
    conn = FakeConn(b"")
    with pytest.raises(mailparts.PartError, match="limit"):
        mailparts.fetch_section(conn, 7, part(10_000_000), 1000)
    assert conn.asked is None


def test_server_sending_more_than_announced_is_refused():
    conn = FakeConn(b"A" * 5000)
    with pytest.raises(mailparts.PartError, match="limit"):
        mailparts.fetch_section(conn, 7, part(10, encoding="7bit"), 1000)


def test_bad_section_is_refused():
    with pytest.raises(mailparts.PartError):
        mailparts.fetch_section(FakeConn(b""), 7, part(10, section="1]<0.1> BODY[2"), 1000)


def test_quoted_printable():
    assert mailparts.decode_part(b"a=3Db=\r\nc", "quoted-printable") == b"a=bc"


def test_decoded_size():
    assert mailparts.decoded_size(part(400)) == 300
    assert mailparts.decoded_size(part(400, encoding="7bit")) == 400


# ---- the helpers -------------------------------------------------------------

def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(BIN / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_runnable_types_are_not_opened(monkeypatch):
    helper = load("mail-attachment")
    script = [b'1 (UID 7 BODYSTRUCTURE (("text" "plain" NIL NIL NIL "7bit" 1 1 NIL NIL NIL)'
              b'("application" "x-sh" NIL NIL NIL "base64" 8 NIL ("attachment" ("filename" "run.sh")) NIL)'
              b' "mixed" NIL NIL NIL))']

    class Conn:
        def uid(self, command, uid, spec):
            assert "BODY.PEEK" not in spec, "must refuse before downloading"
            return "OK", script

    monkeypatch.setattr(helper, "connect", lambda account, readonly=True: (Conn(), "1"))
    monkeypatch.setattr(helper, "close", lambda conn: None)
    with pytest.raises(mailparts.PartError, match="save it instead"):
        helper.run({}, "7", "", 0, True)


def test_html_document_is_reencoded_and_scripts_are_off():
    helper = load("mail-html")
    out = helper.html_document("<p>Grüße</p>".encode("iso-8859-1"), "iso-8859-1")
    text = out.decode("utf-8")
    assert text.startswith('<meta charset="utf-8">')
    assert "script-src 'none'" in text
    assert "<p>Grüße</p>" in text
