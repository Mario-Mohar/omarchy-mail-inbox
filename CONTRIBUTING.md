# Contributing

Thanks for taking the time. This is a small project, so the process is short.

## Getting set up

```bash
git clone https://github.com/Mario-Mohar/omarchy-mail-inbox.git
cd omarchy-mail-inbox
./install
bin/mail-inbox-setup     # the interactive account wizard
```

`install` copies the plugin into `~/.config/omarchy/plugins/themo.mail-inbox/`
and adds its entry to `shell.json`, backing that file up first. `./uninstall`
reverses both. Neither uses `sudo`, neither downloads anything, and neither
writes outside your own directories.

Only `python3` is required at runtime — the standard library, nothing to
install. Passwords go to the login keyring via `secret-tool`; they are never
written to `accounts.json` and never appear on a command line.

## Running the checks

The pipeline runs exactly what you can run here:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install pytest ruff

python3 .github/scripts/check_plugin.py   # manifest.json and textFormat
ruff check .                              # Python lint
pytest tests/ -v                          # tests
shellcheck bin/mail-inbox-setup install uninstall
```

QML syntax is checked with `qmllint` (`qt6-declarative-dev-tools`), which also
reports unresolved Quickshell imports that cannot resolve outside a running
shell. The pipeline only fails on diagnostics tagged `[syntax]`.

## Three rules worth knowing before you write code

**Every `Text` item declares a `textFormat`.** Without one, Qt's default
`AutoText` renders HTML-shaped content as rich text inside the shell process and
can make it load a remote image. A mail subject is attacker-controlled text, so
this matters more here than in most plugins. The rule is deliberately blunt —
*every* `Text`, static labels included — so adding one always forces the
decision. `check_plugin.py` fails on a missing one.

**Everything crossing into the shell is bounded.** The consumer is a QML
`StdioCollector` that buffers a whole response before anything inspects it, so
one mailbox with absurd headers must not be able to grow that buffer without
limit. Go out through `mailcommon.emit()`, which clips strings, lists and
nesting depth on the way. Do not `print()` a payload yourself.

**`accounts.json` is read through a descriptor, not a path.** `mailcommon`
refuses a symlink, a file somebody else owns, one others can read, and one over
its size cap — and it asks every one of those questions of the descriptor it
actually reads from, so nothing can be swapped in between. If you add a helper
that needs the config, call `load_accounts()`; do not open the file yourself.
`tests/test_mailcommon.py` documents what each refusal looks like.

## Pull requests

- Branch off `main`. Any branch name is fine.
- Commit messages follow `fix(scope):`, `feat(scope):`, `docs:`, `chore:`.
  The pipeline reads the pull request title's prefix to label it.
- Say what changed and why. A screenshot helps for anything visible.
- The pipeline comments the result and updates that comment on every push.
  Green plus not-a-draft gets a `ready-to-merge` label.
- Maintainers can ask for a deeper look with `/claude review`.

Tests are welcome but not demanded for every change. A bug fix that comes with
the test that would have caught it is the ideal, not the entry fee.

## Reporting something

Use the issue templates. **Never paste a real password, an app password, or a
full IMAP session.** A redacted `bin/mail-check` line and your provider's name
are usually enough.

## Licence

MIT, same as the project. By contributing you agree your work ships under it.
