"""Shared IMAP/keyring plumbing for the mail-inbox plugin's helper scripts.

Passwords are never passed on the command line and never written to the
config file: they are read from the login keyring via secret-tool at call
time. Message bodies and reply text travel over stdin for the same reason.
"""
import email
import email.header
import email.utils
import errno
import imaplib
import json
import os
import re
import socket
import ssl
import stat
import subprocess
from collections import deque

CONFIG_DIR = os.path.expanduser("~/.config/omarchy/mail-inbox")
ACCOUNTS_FILE = os.path.join(CONFIG_DIR, "accounts.json")
KEYRING_SERVICE = "omarchy-mail-inbox"

CONNECT_TIMEOUT = 20
MAX_FIELD = 300

# Hard ceilings. The config file is trusted-ish (it is the user's own), but a
# corrupted or hostile one must not be able to spend unbounded memory or open an
# unbounded number of connections before anything notices.
MAX_CONFIG_BYTES = 256 * 1024
MAX_ACCOUNTS = 50
MAX_FIELDS_PER_ACCOUNT = 40
MAX_FIELD_VALUE = 2048
# Everything below comes off the network and is attacker-influenced.
MAX_UID_TAIL = 200
MAX_RECIPIENTS = 50
MAX_ATTACHMENT_NAMES = 25

# imaplib refuses very long lines by default; some servers send big FLAGS runs.
imaplib._MAXLINE = max(imaplib._MAXLINE, 200000)
socket.setdefaulttimeout(CONNECT_TIMEOUT)


class AccountError(Exception):
    """One mailbox failed. In --all mode the others must still be reported."""


def die(message):
    raise AccountError(message)


def _read_config_bytes():
    """Read accounts.json through one descriptor, with the checks on that same
    descriptor rather than on the path.

    Checking a path and then opening it is two different files as far as the
    kernel is concerned. O_NOFOLLOW refuses a symlink outright, and every
    property below is asked of the descriptor we actually read from, so nothing
    can be swapped underneath us in between.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(ACCOUNTS_FILE, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            die("accounts.json is a symlink, refusing to read it")
        die("accounts.json unreadable: %s" % exc)

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            die("accounts.json is not a regular file")
        if info.st_uid != os.geteuid():
            die("accounts.json is owned by uid %d, not by you" % info.st_uid)
        if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            die("accounts.json is readable by others — run: chmod 600 %s"
                % ACCOUNTS_FILE)
        if info.st_size > MAX_CONFIG_BYTES:
            die("accounts.json is larger than %d bytes" % MAX_CONFIG_BYTES)

        chunks = []
        remaining = MAX_CONFIG_BYTES + 1
        while remaining > 0:
            block = os.read(fd, min(65536, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        raw = b"".join(chunks)
        if len(raw) > MAX_CONFIG_BYTES:
            die("accounts.json grew past %d bytes while reading"
                % MAX_CONFIG_BYTES)
        return raw
    finally:
        os.close(fd)


def _clip_value(value):
    """Config values end up in log lines and IMAP commands. Bound them."""
    if isinstance(value, str):
        return value[:MAX_FIELD_VALUE]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_FIELD_VALUE]


def _sanitise_accounts(entries):
    """Cap the list and each entry before anything opens a connection."""
    accounts = []
    for entry in entries[:MAX_ACCOUNTS]:
        if not isinstance(entry, dict):
            continue
        clean = {}
        for key in list(entry)[:MAX_FIELDS_PER_ACCOUNT]:
            clean[str(key)[:MAX_FIELD_VALUE]] = _clip_value(entry[key])
        accounts.append(clean)
    return accounts


def load_accounts():
    raw = _read_config_bytes()
    if raw is None:
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        die("accounts.json unreadable: %s" % exc)
    entries = data.get("accounts") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    return _sanitise_accounts(entries)


def find_account(account_id):
    for entry in load_accounts():
        if isinstance(entry, dict) and str(entry.get("id", "")) == account_id:
            return entry
    return None


def require_account(account_id):
    account = find_account(account_id)
    if account is None:
        die("no accounts configured — run mail-inbox-account add"
            if not load_accounts() else "unknown account id '%s'" % account_id)
    return account


def keyring_password(account_id):
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", KEYRING_SERVICE,
             "account", account_id],
            capture_output=True, timeout=15)
    except FileNotFoundError:
        return None, "secret-tool not installed (package libsecret)"
    except subprocess.TimeoutExpired:
        return None, "keyring locked or not responding"
    if result.returncode != 0 or not result.stdout:
        return None, "no password in keyring for '%s'" % account_id
    return result.stdout.decode("utf-8", "replace").rstrip("\n"), ""


def require_password(account_id):
    password, err = keyring_password(account_id)
    if password is None:
        die(err)
    return password


def connect(account, readonly=True, folder=None):
    """Log in and select the account's folder. Returns (conn, uidvalidity)."""
    host = str(account.get("host", "")).strip()
    user = str(account.get("user", "")).strip()
    port = int(account.get("port", 993) or 993)
    account_id = str(account.get("id", ""))
    box = str(folder if folder is not None
              else account.get("folder", "INBOX")).strip() or "INBOX"

    if not host or not user:
        die("account is missing host or user")

    password = require_password(account_id)
    context = ssl.create_default_context()
    try:
        if int(account.get("starttls", 0)):
            conn = imaplib.IMAP4(host, port or 143)
            conn.starttls(ssl_context=context)
        else:
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=context)
        conn.login(user, password)
    except imaplib.IMAP4.error as exc:
        raise_imap(exc)
    except (socket.timeout, TimeoutError):
        die("timed out talking to %s" % host)
    except (socket.gaierror, ConnectionError, ssl.SSLError, OSError) as exc:
        die("connection failed: %s" % str(exc)[:200])

    try:
        status, _ = conn.select('"%s"' % box.replace('"', ''), readonly=readonly)
        if status != "OK":
            die("cannot open folder '%s'" % box)
    except imaplib.IMAP4.error as exc:
        close(conn)
        raise_imap(exc)
    except AccountError:
        close(conn)
        raise

    validity = ""
    try:
        raw = conn.response("UIDVALIDITY")[1]
        if raw and raw[0]:
            validity = raw[0].decode("ascii", "replace")
    except Exception:
        pass
    return conn, validity


def raise_imap(exc):
    text = str(exc)
    if "AUTHENTICATIONFAILED" in text.upper() or "Invalid credentials" in text:
        die("login rejected — check the app password")
    die("IMAP error: %s" % text[:200])


def close(conn):
    if conn is None:
        return
    try:
        conn.logout()
    except Exception:
        pass


def decode_field(raw, limit=MAX_FIELD):
    if not raw:
        return ""
    try:
        text = str(email.header.make_header(email.header.decode_header(raw)))
    except (UnicodeDecodeError, LookupError, ValueError):
        text = str(raw)
    text = " ".join(text.split())
    return text[:limit] if limit else text


def sender_name(raw):
    name, addr = email.utils.parseaddr(decode_field(raw))
    return name or addr or "(unknown)"


def iso_date(raw):
    try:
        parsed = email.utils.parsedate_to_datetime(decode_field(raw))
    except (TypeError, ValueError):
        return ""
    if parsed is None:
        return ""
    return parsed.astimezone().isoformat()


MAX_EMIT_BYTES = 1024 * 1024


def _bound(value, depth=0):
    """Cap every string and list on the way out.

    The consumer is a QML StdioCollector that buffers whatever arrives before
    anything gets to look at it. The cheapest place to bound that buffer is
    here, at the producer, where the shape of the data is still known.
    """
    if depth > 8:
        return None
    if isinstance(value, str):
        return value[:MAX_FIELD_VALUE]
    if isinstance(value, dict):
        return {str(k)[:MAX_FIELD_VALUE]: _bound(v, depth + 1)
                for k, v in list(value.items())[:MAX_FIELDS_PER_ACCOUNT]}
    if isinstance(value, list):
        return [_bound(v, depth + 1) for v in value[:MAX_ACCOUNTS]]
    return value


def emit(payload):
    import sys
    text = json.dumps(_bound(payload), ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_EMIT_BYTES:
        text = json.dumps({"error": "response too large, refusing to emit"})
    sys.stdout.write(text)
    sys.stdout.write("\n")
