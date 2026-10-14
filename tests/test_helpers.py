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
    assert result["totalUnread"] == 0 and result["accounts"] == []
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
