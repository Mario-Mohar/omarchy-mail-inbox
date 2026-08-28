"""Shared IMAP/keyring plumbing for the mail-inbox plugin's helper scripts.

Passwords are never passed on the command line and never written to the
config file: they are read from the login keyring via secret-tool at call
time. Message bodies and reply text travel over stdin for the same reason.
"""
import email
import email.header
import email.utils
import imaplib
import json
import os
import socket
import ssl
import subprocess

CONFIG_DIR = os.path.expanduser("~/.config/omarchy/mail-inbox")
ACCOUNTS_FILE = os.path.join(CONFIG_DIR, "accounts.json")
KEYRING_SERVICE = "omarchy-mail-inbox"

CONNECT_TIMEOUT = 20
MAX_FIELD = 300

# imaplib refuses very long lines by default; some servers send big FLAGS runs.
imaplib._MAXLINE = max(imaplib._MAXLINE, 200000)
socket.setdefaulttimeout(CONNECT_TIMEOUT)


class AccountError(Exception):
    """One mailbox failed. In --all mode the others must still be reported."""


def die(message):
    raise AccountError(message)


def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        die("accounts.json unreadable: %s" % exc)
    accounts = data.get("accounts") if isinstance(data, dict) else data
    return accounts if isinstance(accounts, list) else []


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


def emit(payload):
    import sys
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
