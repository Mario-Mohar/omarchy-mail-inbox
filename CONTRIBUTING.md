# Contributing

## Contributions are welcome

This is a small project maintained by one person in his spare time, and that is
exactly why an outside pair of eyes is worth a lot. **Finding a bug and writing
it down is a real contribution** — arguably the most useful one, because I only
ever use this on my own machine, with my own setup, and most of what is broken
is broken somewhere I never look.

Three ways to help, in the order of what they cost you:

### 1. Report something that is wrong

Open an issue with the **Bug report** template. It asks for what it does because
each field is something I would otherwise have to come back and ask for, which
costs us both a day.

What actually decides whether a report is useful:

- **What you expected, and what happened instead.** Both halves. "It does not
  work" is the one report I cannot act on.
- **The steps that get there.** If you can reproduce it, say how. If it only
  happened once, say that too — an intermittent bug is still worth knowing about,
  and "I could not reproduce it" is useful information rather than a
  disqualification.
- **Your setup**, as the template asks for it.

Do not polish it. A rough report today beats a perfect one that never gets
written. If in doubt whether something counts as a bug: open it. Deciding that
is my job, not yours.

### 2. Suggest something it should do

Open an issue with the **Feature request** template.

It asks what you are trying to *achieve* before what you want built, and that is
deliberate — not a hoop. Roughly half the time there turns out to be a simpler
answer than the one either of us had in mind, and it only surfaces if I know the
underlying situation.

A wish that gets declined is not a wasted issue. "Not now" and "not in this
project" are answers you will get quickly and with a reason.

### 3. Send a fix or a feature

Very welcome, and you do not need to ask permission for something small.

**For anything bigger than a few lines, open an issue first** — or comment on
the existing one — and say you are working on it. It costs you a sentence and
saves you the case where I fixed the same thing that evening, or where I would
have wanted it solved differently.

Because you cannot push to this repository, the route is through a fork:

```bash
# 1. Fork it on GitHub, then clone your fork
git clone https://github.com/<your-username>/omarchy-mail-inbox.git
cd omarchy-mail-inbox

# 2. A branch. Any name.
git switch -c fix/the-thing

# 3. Change what you came for, then run the checks below

# 4. Push to your fork and open the pull request
git push -u origin fix/the-thing
```

GitHub then offers you the pull request button. Fill in the template, and if it
closes an issue write `Fixes #12` so it closes itself on merge.

## What happens after you send it

1. **The pipeline runs** and posts a comment on your pull request with a table
   of what passed. It updates that same comment on every push, so there is one
   place to look rather than a growing pile.
2. **It labels the pull request** by size and type, and adds `ready-to-merge`
   once everything is green.
3. **On your very first contribution here, the checks wait for me to release
   them.** GitHub does that by default so that a stranger's code cannot use the
   runners unasked. If your pull request sits at "waiting for approval",
   **nothing is broken and you do not need to do anything** — I have to click
   once.
4. **I do the merging.** The default branch takes nothing that has not been
   through a pull request with green checks, and that holds for my own commits
   too.

If a check is red, the run log says which one and why. Ask in the pull request
if it is not obvious — a red pipeline is not a rejection, and quite often it is
the pipeline that is wrong rather than you.

I do this beside a job, so a reply can take a few days. It is not disinterest.

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

- Branch off `main` **in your fork** (see above). Any branch name is fine.
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
