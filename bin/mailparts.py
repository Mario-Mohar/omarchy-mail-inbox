"""MIME structure, attachment names and where fetched parts end up on disk.

mail-read lists the attachments of a message and mail-attachment / mail-html
fetch one part of it. Both work from the server's BODYSTRUCTURE rather than
from the downloaded text: mail-read only pulls the first 256 KB, and an
attachment behind that point would otherwise be invisible. Fetching one part
by its section number also means a 30 MB attachment is never downloaded just
to open the 2 KB one next to it.

Everything in a BODYSTRUCTURE comes off the network, so the parser is bounded
in depth and in the number of parts it will look at, and the file name a mail
suggests is treated as hostile until sanitise_filename has been through it.
"""

import base64
import binascii
import os
import quopri
import re
import stat
import subprocess
import tempfile
import time
import unicodedata
import urllib.parse

from mailcommon import decode_field

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_PARTS = 200
MAX_DEPTH = 12
MAX_NAME_BYTES = 120

# Types xdg-open would hand to something that runs them, or that only exist to
# be run. Saving stays allowed: that is a deliberate step, opening is a click.
NEVER_OPEN = {
    "appimage", "bash", "bat", "bin", "cmd", "com", "deb", "desktop", "exe",
    "jar", "js", "lnk", "msi", "pkg", "pl", "ps1", "py", "rb", "rpm", "run",
    "scr", "sh", "vbs", "zsh",
}

PRIVATE_DIR_NAME = "omarchy-mail-inbox"
PRIVATE_MAX_AGE = 24 * 3600


class PartError(Exception):
    """A part could not be found, fetched or stored; the text is for the user."""


# ---- BODYSTRUCTURE -----------------------------------------------------------

TOKEN = re.compile(rb'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|\{(\d+)\}$|([^\s()"]+))', re.S)


def _segments(fetch_payload):
    """imaplib's FETCH answer as a flat list of ('text', b) and ('lit', b)."""
    out = []
    for item in fetch_payload or []:
        if isinstance(item, tuple) and len(item) >= 2:
            out.append(("text", bytes(item[0])))
            out.append(("lit", bytes(item[1])))
        elif isinstance(item, (bytes, bytearray)):
            out.append(("text", bytes(item)))
    return out


def _tokens(segments):
    for kind, data in segments:
        if kind == "lit":
            yield ("str", data)
            continue
        pos = 0
        while pos < len(data):
            match = TOKEN.match(data, pos)
            if not match or match.end() == pos:
                break
            pos = match.end()
            if match.group(1):
                yield ("open", None)
            elif match.group(2):
                yield ("close", None)
            elif match.group(3) is not None:
                yield ("str", re.sub(rb"\\(.)", rb"\1", match.group(3)))
            elif match.group(4) is not None:
                pass  # the literal follows as its own segment
            elif match.group(5):
                yield ("atom", match.group(5))


def _tree(tokens):
    """Nested lists from the token stream; strings decoded, NIL as None."""
    root, stack = [], []
    current = root
    for kind, value in tokens:
        if kind == "open":
            if len(stack) >= MAX_DEPTH * 4:
                raise PartError("message structure is nested too deeply")
            child = []
            current.append(child)
            stack.append(current)
            current = child
        elif kind == "close":
            if not stack:
                break
            current = stack.pop()
        elif kind == "atom" and value.upper() == b"NIL":
            current.append(None)
        else:
            current.append(value.decode("utf-8", "replace"))
    return root


def parse_bodystructure(fetch_payload):
    """The BODYSTRUCTURE list out of a UID FETCH answer, or None."""
    tree = _tree(_tokens(_segments(fetch_payload)))
    # Shape: [ <msgno>, [ 'UID', '5', 'BODYSTRUCTURE', [...] ] ]
    for node in tree:
        if isinstance(node, list):
            for i, value in enumerate(node[:-1]):
                if isinstance(value, str) and value.upper() == "BODYSTRUCTURE":
                    if isinstance(node[i + 1], list):
                        return node[i + 1]
    return None


def _params(value):
    """('name' 'a.pdf' 'charset' 'x') -> {'name': 'a.pdf', ...}, keys lowercased."""
    out = {}
    if isinstance(value, list):
        for i in range(0, len(value) - 1, 2):
            if isinstance(value[i], str) and isinstance(value[i + 1], str):
                out[value[i].lower()] = value[i + 1]
    return out


def _rfc2231(params, key):
    """filename / filename* / filename*0*... as one decoded string."""
    if key + "*" in params:
        return _decode_2231(params[key + "*"], True)
    pieces, n = [], 0
    while n < 100:
        if key + "*%d*" % n in params:
            pieces.append((params[key + "*%d*" % n], True))
        elif key + "*%d" % n in params:
            pieces.append((params[key + "*%d" % n], False))
        else:
            break
        n += 1
    if pieces:
        charset = "utf-8"
        raw = ""
        for index, (text, encoded) in enumerate(pieces):
            if encoded and index == 0 and text.count("'") >= 2:
                charset, _, text = text.split("'", 2)
            raw += urllib.parse.unquote(text, encoding=charset or "utf-8", errors="replace") \
                if encoded else text
        return raw
    if key in params:
        return decode_field(params[key], limit=0)
    return ""


def _decode_2231(value, encoded):
    if encoded and value.count("'") >= 2:
        charset, _, text = value.split("'", 2)
        try:
            return urllib.parse.unquote(text, encoding=charset or "utf-8", errors="replace")
        except LookupError:
            return urllib.parse.unquote(text, errors="replace")
    return urllib.parse.unquote(value, errors="replace")


def _single(node, section):
    """One leaf of the structure as a dict."""
    def at(i):
        return node[i] if len(node) > i else None

    ctype = ("%s/%s" % (at(0) or "", at(1) or "")).lower()
    params = _params(at(2))
    encoding = str(at(5) or "7bit").lower()
    try:
        size = int(at(6) or 0)
    except (TypeError, ValueError):
        size = 0
    if ctype.startswith("text/"):
        disposition = at(9)
    elif ctype == "message/rfc822":
        disposition = at(11)
    else:
        disposition = at(8)
    disp_type, disp_params = "", {}
    if isinstance(disposition, list) and disposition:
        disp_type = str(disposition[0] or "").lower()
        disp_params = _params(disposition[1] if len(disposition) > 1 else None)
    name = _rfc2231(disp_params, "filename") or _rfc2231(params, "name")
    return {
        "section": section,
        "type": ctype,
        "encoding": encoding,
        "size": size,
        "charset": params.get("charset", ""),
        "disposition": disp_type,
        "name": name,
    }


def leaves(structure):
    """Every leaf part with its IMAP section number, in MIME order."""
    out = []

    def walk(node, section, depth):
        if len(out) >= MAX_PARTS or depth > MAX_DEPTH or not isinstance(node, list):
            return
        if node and isinstance(node[0], list):
            index = 0
            for child in node:
                if not isinstance(child, list):
                    break  # the subtype and extension data follow the children
                index += 1
                walk(child, "%s.%d" % (section, index) if section else str(index), depth + 1)
        else:
            out.append(_single(node, section or "1"))

    walk(structure, "", 0)
    return out


def attachments(parts):
    """The parts a reader would call attachments, same rule as mail-read used."""
    return [p for p in parts
            if p["disposition"] == "attachment" or (p["name"] and not p["type"].startswith("multipart/"))]


def first_html(parts):
    for p in parts:
        if p["type"] == "text/html" and p["disposition"] != "attachment" and not p["name"]:
            return p
    return None


# ---- decoding ----------------------------------------------------------------

def decode_part(raw, encoding):
    encoding = (encoding or "").lower()
    if encoding == "base64":
        try:
            return base64.b64decode(re.sub(rb"[^A-Za-z0-9+/=]", b"", raw) + b"==", validate=False)
        except (binascii.Error, ValueError) as exc:
            raise PartError("attachment is not valid base64") from exc
    if encoding == "quoted-printable":
        return quopri.decodestring(raw)
    return raw


def encoded_limit(decoded_limit, encoding):
    """How many raw bytes to fetch at most for a decoded size limit."""
    if (encoding or "").lower() == "base64":
        # 4 chars per 3 bytes plus a line break every 76 characters.
        return decoded_limit * 4 // 3 + decoded_limit // 50 + 1024
    if (encoding or "").lower() == "quoted-printable":
        return decoded_limit * 3 + 1024
    return decoded_limit + 1024


def decoded_size(part):
    """BODYSTRUCTURE counts the encoded bytes; base64 adds a third on top."""
    if part["encoding"] == "base64":
        return part["size"] * 3 // 4
    return part["size"]


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%d B" % n


# ---- file names --------------------------------------------------------------

def sanitise_filename(name, fallback="attachment"):
    """A name that is safe to create in a directory the user browses.

    No path component, no control or format characters (a right-to-left
    override can make 'gpj.exe' read as 'exe.jpg'), no leading dot that would
    hide the file, bounded length that keeps the extension.
    """
    name = str(name or "")
    name = re.split(r"[/\\]", name)[-1]
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co"))
    name = " ".join(name.split())
    name = name.lstrip(". ").rstrip(". ")
    if not name:
        name = fallback
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        stem, ext = os.path.splitext(name)
        if len(ext.encode("utf-8")) > 16:
            stem, ext = name, ""
        budget = MAX_NAME_BYTES - len(ext.encode("utf-8"))
        stem = stem.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip(". ")
        name = (stem or fallback) + ext
    return name


def extension(name):
    return os.path.splitext(name)[1].lstrip(".").lower()


def download_dir():
    """XDG download directory; ~/Downloads when it is not configured."""
    home = os.path.expanduser("~")
    path = ""
    try:
        path = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        path = ""
    # xdg-user-dir answers with $HOME when nothing is set, and the home folder
    # is not where a stray attachment should land.
    if not path or os.path.realpath(path) == os.path.realpath(home):
        path = os.path.join(home, "Downloads")
    os.makedirs(path, exist_ok=True)
    return path


def write_new(directory, name, data, mode=0o600):
    """Create name in directory without ever replacing anything.

    'report.pdf' becomes 'report (2).pdf' when taken. O_EXCL makes the check
    and the creation one step, and refuses a planted symlink as well.
    """
    stem, ext = os.path.splitext(name)
    for n in range(1, 1000):
        candidate = name if n == 1 else "%s (%d)%s" % (stem, n, ext)
        path = os.path.join(directory, candidate)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return path
    raise PartError("could not find a free file name for %s" % name)


def private_dir():
    """A per-user directory only the user can enter, for files that get opened.

    $XDG_RUNTIME_DIR is a tmpfs owned by the user and gone after logout, which
    is the right lifetime for a mail that was only meant to be looked at.
    Anything older than a day is cleared out on the way in.
    """
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    path = os.path.join(base, PRIVATE_DIR_NAME)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PartError("%s is not a private directory, refusing to use it" % path)
    os.chmod(path, 0o700)
    _prune(path)
    return tempfile.mkdtemp(dir=path)


def _prune(path):
    cutoff = time.time() - PRIVATE_MAX_AGE
    for entry in os.scandir(path):
        try:
            if entry.is_dir(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < cutoff:
                for child in os.scandir(entry.path):
                    os.unlink(child.path)
                os.rmdir(entry.path)
        except OSError:
            continue


def open_detached(path):
    subprocess.Popen(["xdg-open", path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# ---- IMAP --------------------------------------------------------------------

def fetch_parts(conn, uid):
    status, payload = conn.uid("FETCH", str(uid), "(BODYSTRUCTURE)")
    if status != "OK" or not payload or payload == [None]:
        raise PartError("message %s is gone from the mailbox" % uid)
    structure = parse_bodystructure(payload)
    if structure is None:
        raise PartError("the server did not describe message %s" % uid)
    return leaves(structure)


def fetch_section(conn, uid, part, decoded_limit):
    """The decoded bytes of one part, refusing anything over decoded_limit.

    The partial fetch bounds what crosses the network even when the size the
    server announced was wrong, and the check after decoding bounds the rest.
    """
    if part["size"] > encoded_limit(decoded_limit, part["encoding"]):
        raise PartError("%s is %s, over the %s limit; open it in a mail client"
                        % (part["name"] or "this part", human_size(part["size"]),
                           human_size(decoded_limit)))
    if not re.match(r"^\d+(\.\d+)*$", part["section"]):
        raise PartError("invalid part number")
    limit = encoded_limit(decoded_limit, part["encoding"]) + 1
    status, payload = conn.uid("FETCH", str(uid),
                               "(BODY.PEEK[%s]<0.%d>)" % (part["section"], limit))
    if status != "OK" or not payload:
        raise PartError("part %s could not be fetched" % part["section"])
    raw = b""
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            raw = bytes(item[1])
            break
    if len(raw) >= limit:
        raise PartError("%s is larger than the %s limit" % (part["name"] or "this part",
                                                           human_size(decoded_limit)))
    data = decode_part(raw, part["encoding"])
    if len(data) > decoded_limit:
        raise PartError("%s is larger than the %s limit" % (part["name"] or "this part",
                                                           human_size(decoded_limit)))
    return data
