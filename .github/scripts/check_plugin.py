#!/usr/bin/env python3
"""Checks an Omarchy plugin repository the way the marketplace will.

Two things are verified here that nothing else catches:

manifest.json -- the marketplace rejects a manifest whose declared entry points
do not exist, whose settings schema and defaults disagree, or whose id and
version are malformed. Finding that out from a submission issue is a slow way
to learn it.

textFormat -- a Text item without an explicit textFormat keeps Qt's default
AutoText, which renders HTML-shaped content as rich text inside the shell
process and can make it load a remote image. Any Text that shows a value from
an upstream API is a hole. The rule is deliberately blunt: every Text declares
its format, so a new one cannot be added without deciding.

Run it from the repository root:  python3 .github/scripts/check_plugin.py
"""

import json
import re
import sys
from pathlib import Path

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
TEXT_ITEM = re.compile(r"^(\s*)Text\s*\{")

REQUIRED_KEYS = ["schemaVersion", "id", "name", "version", "author",
                 "license", "description", "kinds", "entryPoints"]


class Report:
    def __init__(self):
        self.problems = []

    def fail(self, where, message):
        self.problems.append("%s: %s" % (where, message))

    def ok(self):
        return not self.problems


def check_manifest(root, report):
    path = root / "manifest.json"
    if not path.is_file():
        report.fail("manifest.json", "missing")
        return

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.fail("manifest.json", "is not valid JSON: %s" % exc)
        return

    for key in REQUIRED_KEYS:
        if key not in manifest:
            report.fail("manifest.json", "required key %r is missing" % key)

    plugin_id = manifest.get("id", "")
    if plugin_id and not ID_PATTERN.match(plugin_id):
        report.fail("manifest.json", "id %r is not <vendor>.<name> in lowercase" % plugin_id)

    version = manifest.get("version", "")
    if version and not SEMVER_PATTERN.match(version):
        report.fail("manifest.json", "version %r is not semver" % version)

    for name, target in (manifest.get("entryPoints") or {}).items():
        if not (root / target).is_file():
            report.fail("manifest.json", "entryPoint %s points at %s, which does not exist"
                        % (name, target))

    for kind in ("barWidget", "panel", "service"):
        section = manifest.get(kind)
        if isinstance(section, dict):
            check_settings_schema(kind, section, report)


def check_settings_schema(kind, section, report):
    """defaults and schema have to describe the same settings, with the same values."""
    schema = section.get("schema") or []
    defaults = section.get("defaults") or {}

    schema_keys = set()
    for entry in schema:
        key = entry.get("key")
        if not key:
            report.fail("manifest.json", "%s.schema has an entry without a key" % kind)
            continue
        schema_keys.add(key)

        if "defaultValue" not in entry:
            report.fail("manifest.json", "%s.schema[%s] has no defaultValue" % (kind, key))
        elif key in defaults and defaults[key] != entry["defaultValue"]:
            report.fail("manifest.json",
                        "%s: defaults[%s] is %r but schema says %r"
                        % (kind, key, defaults[key], entry["defaultValue"]))

        if entry.get("type") == "enum":
            values = [o.get("value") for o in entry.get("options") or []]
            if not values:
                report.fail("manifest.json", "%s.schema[%s] is an enum with no options"
                            % (kind, key))
            elif entry.get("defaultValue") not in values:
                report.fail("manifest.json",
                            "%s.schema[%s] defaults to %r, which is not one of its options %r"
                            % (kind, key, entry.get("defaultValue"), values))

        if entry.get("type") == "integer":
            low, high = entry.get("min"), entry.get("max")
            value = entry.get("defaultValue")
            if isinstance(value, int) and isinstance(low, int) and value < low:
                report.fail("manifest.json", "%s.schema[%s] defaults below its own min"
                            % (kind, key))
            if isinstance(value, int) and isinstance(high, int) and value > high:
                report.fail("manifest.json", "%s.schema[%s] defaults above its own max"
                            % (kind, key))

    for key in sorted(set(defaults) - schema_keys):
        report.fail("manifest.json",
                    "%s.defaults has %r, which the schema does not describe" % (kind, key))


def check_text_format(root, report):
    for path in sorted(root.rglob("*.qml")):
        if ".git" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            match = TEXT_ITEM.match(line)
            if not match:
                continue
            if not declares_text_format(lines, index, len(match.group(1))):
                report.fail("%s:%d" % (path.relative_to(root), index + 1),
                            "Text item without textFormat -- add "
                            "`textFormat: Text.PlainText` (or another explicit format)")


def declares_text_format(lines, start, indent):
    """Look through the body of the Text item that opens on `lines[start]`."""
    depth = 0
    for line in lines[start:]:
        depth += line.count("{") - line.count("}")
        if "textFormat:" in line:
            return True
        if depth <= 0:
            break
    return False


def main():
    root = Path(__file__).resolve().parent.parent.parent
    report = Report()

    check_manifest(root, report)
    check_text_format(root, report)

    if report.ok():
        print("plugin checks passed")
        return 0

    print("plugin checks failed:\n")
    for problem in report.problems:
        print("  - %s" % problem)
    return 1


if __name__ == "__main__":
    sys.exit(main())
