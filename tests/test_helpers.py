"""Tests for the executables in bin/, without a mailbox or a keyring.

Each helper runs either as a subprocess with HOME pointed at a temporary
directory, or is loaded as a module with its network calls replaced. Nothing
here reads the real accounts.json or talks to a server.
"""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(BIN / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def fake_home(tmp_path, accounts, mode=0o600):
    """A HOME with accounts.json in it, written with the given mode."""
    config = tmp_path / "home" / ".config" / "omarchy" / "mail-inbox"
    config.mkdir(parents=True, mode=0o700)
    path = config / "accounts.json"
    path.write_text(accounts if isinstance(accounts, str) else json.dumps(accounts))
    os.chmod(path, mode)
    return tmp_path / "home"


def run(name, args, home, stdin=""):
    env = dict(os.environ, HOME=str(home))
    return subprocess.run([sys.executable, str(BIN / name)] + args, input=stdin,
                          capture_output=True, text=True, env=env, timeout=30)


# ---- #11: an unreadable accounts.json -------------------------------------------

def test_check_all_reports_an_unreadable_config_as_json(tmp_path):
    home = fake_home(tmp_path, {"accounts": [{"id": "work"}]}, mode=0o644)
    proc = run("mail-check", ["--all"], home)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert "accounts" not in result  # so the widget keeps the last counts
    assert "chmod 600" in result["error"]


def test_check_one_account_reports_broken_json(tmp_path):
    home = fake_home(tmp_path, "{not json")
    proc = run("mail-check", ["--account", "work"], home)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["id"] == "work" and result["unread"] == 0
    assert "unreadable" in result["error"]


def test_account_test_fails_on_an_unreadable_config(tmp_path):
    home = fake_home(tmp_path, {"accounts": [{"id": "work"}]}, mode=0o644)
    proc = run("mail-inbox-account", ["test", "work"], home)
    assert proc.returncode != 0
    assert "FAILED" in proc.stderr and "OK" not in proc.stdout


def test_account_test_fails_when_mail_check_crashes(monkeypatch, capsys):
    helper = load("mail-inbox-account")

    def crashed(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, b"", b"Traceback\nKeyError: 'x'\n")

    monkeypatch.setattr(helper.subprocess, "run", crashed)
    with pytest.raises(SystemExit) as exc:
        helper.cmd_test(helper.argparse.Namespace(id="work"))
    assert "FAILED" in str(exc.value) and "KeyError" in str(exc.value)
    assert "OK" not in capsys.readouterr().out


# ---- #12: "Add a mailbox" on a fresh install -------------------------------------

def test_setup_without_accounts_starts_the_wizard(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.run(["bash", str(BIN / "mail-inbox-setup")], input="",
                          capture_output=True, text=True, timeout=30,
                          env=dict(os.environ, HOME=str(home)))
    assert "FileNotFoundError" not in proc.stderr
    assert "Adding a mailbox" in proc.stdout
    assert not (home / ".config" / "omarchy" / "mail-inbox" / "accounts.json").exists()


def test_setup_explains_an_unreadable_config(tmp_path):
    home = fake_home(tmp_path, {"accounts": [{"id": "work", "user": "a@b"}]}, mode=0o644)
    proc = subprocess.run(["bash", str(BIN / "mail-inbox-setup")], input="",
                          capture_output=True, text=True, timeout=30,
                          env=dict(os.environ, HOME=str(home)))
    assert proc.returncode == 1
    assert "chmod 600" in proc.stderr and "Traceback" not in proc.stderr


# ---- #15: References in long threads -----------------------------------------------

def thread(count):
    return ["<%04d.reply@mail.example.com>" % i for i in range(count)]


def test_references_of_a_long_thread_survive_emit(capsys):
    import mailcommon

    reader = load("mail-read")
    ids = thread(100)
    refs = reader.thread_references(" ".join(ids[:-1]), ids[-1])
    assert len(refs) > mailcommon.MAX_FIELD_VALUE
    mailcommon.emit({"references": refs})
    assert json.loads(capsys.readouterr().out)["references"].split() == ids


def test_references_are_trimmed_on_id_boundaries():
    reader = load("mail-read")
    ids = thread(400)
    refs = reader.thread_references(" ".join(ids[:-1]), ids[-1], limit=2000)
    kept = refs.split()
    assert len(refs) <= 2000
    assert kept[0] == ids[0]           # the thread root
    assert kept[-1] == ids[-1]         # the message being answered
    assert kept[1:] == ids[-(len(kept) - 1):]
    assert all(r.startswith("<") and r.endswith(">") for r in kept)


def test_an_oversized_root_id_does_not_crowd_out_the_newest():
    reader = load("mail-read")
    refs = reader.thread_references("<%s@x> <b@x>" % ("a" * 900), "<c@x>", limit=600)
    assert refs == "<b@x> <c@x>"
