"""Turn a mail body into something readable in the panel.

Two steps, both pure functions so they can be tested without a mailbox.

html_to_text flattens an HTML part, but keeps what a reader needs from the
markup: link targets, list bullets, headings and bold. It writes them as the
same light Markdown that GitHub, Codecov and most newsletter tools already put
into their text/plain parts, so both kinds of mail arrive in one shape.

render_html turns that text into the rich text the reader view shows. Every
byte of the mail is escaped first; the only markup in the result is markup this
module wrote: <a> for http, https and mailto targets, <b>, <br> and <span>
with a class. No <img>, no style attribute from the mail, nothing Qt could use
to fetch a remote resource.
"""

import html
import re

# ---- HTML part -> text ------------------------------------------------------

TAG_DROP = re.compile(r"(?is)<\s*(script|style|head|title)[^>]*>.*?<\s*/\s*\1\s*>")
COMMENT = re.compile(r"(?s)<!--.*?-->")
ANCHOR = re.compile(r"""(?is)<\s*a\b[^>]*?\bhref\s*=\s*(["'])(.*?)\1[^>]*>(.*?)<\s*/\s*a\s*>""")
HEADING = re.compile(r"(?is)<\s*h([1-6])\b[^>]*>(.*?)<\s*/\s*h\1\s*>")
STRONG = re.compile(r"(?is)<\s*(b|strong)\b[^>]*>(.*?)<\s*/\s*\1\s*>")
LIST_ITEM = re.compile(r"(?i)<\s*li\b[^>]*>")
BLOCK_BREAK = re.compile(r"(?i)<\s*(br|hr)\b[^>]*>|<\s*/\s*(p|div|tr|ul|ol|table|blockquote|h[1-6])\s*>")
BLOCK_OPEN = re.compile(r"(?i)<\s*(p|div|table|ul|ol|blockquote)\b[^>]*>")
CELL_END = re.compile(r"(?i)<\s*/\s*t[dh]\s*>")
TAG_ANY = re.compile(r"(?s)<[^>]+>")
INLINE_SPACE = re.compile(r"[ \t ​‌‍﻿͏]+")
BLANK_RUN = re.compile(r"\n{3,}")

LINK_SCHEME = re.compile(r"(?i)^(https?://|mailto:)")


def _inner_text(fragment):
    """Visible text of a fragment, on one line."""
    return " ".join(html.unescape(TAG_ANY.sub(" ", fragment)).split())


def _anchor(match):
    href = html.unescape(match.group(2)).strip()
    label = _inner_text(match.group(3))
    if not LINK_SCHEME.match(href) or any(c in href for c in " \n\t<>"):
        return label
    if not label:
        # An image-only link (a banner, a button graphic) still deserves to be
        # reachable; the bare target is the only label there is.
        return " %s " % href
    if label == href or label == href.removeprefix("mailto:"):
        return " %s " % href
    return "[%s](%s)" % (label.replace("[", "(").replace("]", ")"), href)


def _heading(match):
    text = _inner_text(match.group(2))
    return "\n\n%s %s\n\n" % ("#" * int(match.group(1)), text) if text else "\n"


def _strong(match):
    text = _inner_text(match.group(2))
    return "**%s**" % text if text else ""


def html_to_text(raw):
    text = COMMENT.sub(" ", raw)
    text = TAG_DROP.sub(" ", text)
    # Source whitespace means nothing in HTML; the structure comes from tags.
    text = re.sub(r"\s+", " ", text)
    text = ANCHOR.sub(_anchor, text)
    text = HEADING.sub(_heading, text)
    text = STRONG.sub(_strong, text)
    text = LIST_ITEM.sub("\n• ", text)
    text = CELL_END.sub("  ", text)
    text = BLOCK_OPEN.sub("\n", text)
    text = BLOCK_BREAK.sub("\n", text)
    text = TAG_ANY.sub("", text)
    text = html.unescape(text)
    lines = [INLINE_SPACE.sub(" ", line).strip() for line in text.splitlines()]
    return BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


# ---- text -> rich text for the reader ---------------------------------------

MD_LINK = r"\[(?P<label>[^\[\]\n]{1,300})\]\((?P<target>(?:https?://|mailto:)[^\s()<>]+)\)"
ANGLE_URL = r"<(?P<angle>https?://[^\s<>]+)>"
BARE_URL = r"(?P<url>https?://[^\s<>\"'\[\]]+)"
BOLD = r"\*\*(?P<bold>[^*\n]{1,300}?)\*\*"
INLINE = re.compile("|".join((MD_LINK, ANGLE_URL, BARE_URL, BOLD)))

HEADING_LINE = re.compile(r"^#{1,6}\s*(?P<text>.+?)\s*#*\s*$")
UNDERLINE = re.compile(r"^\s*(?:-{3,}|={3,})\s*$")
TRAILING_PUNCT = ".,;:!?)'\""
MAX_URL_LABEL = 60


def _url_label(url):
    """Long tracking links are unreadable; show where they go, not all of it."""
    shown = re.sub(r"(?i)^https?://(www\.)?", "", url)
    if len(shown) <= MAX_URL_LABEL:
        return shown
    return shown[:MAX_URL_LABEL - 1] + "…"


def _link(target, label):
    return '<a href="%s">%s</a>' % (html.escape(target, quote=True), html.escape(label))


def _split_trailing(url):
    """A sentence that ends in a link puts its full stop on the URL."""
    tail = ""
    while url and url[-1] in TRAILING_PUNCT:
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        tail = url[-1] + tail
        url = url[:-1]
    return url, tail


def _inline(line):
    out, pos = [], 0
    for match in INLINE.finditer(line):
        out.append(html.escape(line[pos:match.start()]))
        pos = match.end()
        if match.group("target"):
            out.append(_link(match.group("target"), match.group("label").strip()))
        elif match.group("angle"):
            url = match.group("angle")
            out.append(_link(url, _url_label(url)))
        elif match.group("url"):
            url, tail = _split_trailing(match.group("url"))
            out.append(_link(url, _url_label(url)) if url else "")
            out.append(html.escape(tail))
        else:
            out.append("<b>%s</b>" % _inline(match.group("bold")))
    out.append(html.escape(line[pos:]))
    return "".join(out)


def render_html(text):
    """Escaped rich text with clickable links; '' when there is nothing to show."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    rendered, signature = [], False
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if UNDERLINE.match(line) and index > 0 and lines[index - 1].strip():
            continue  # setext underline; the line above was already made bold
        if line == "--":
            signature = True
        heading = HEADING_LINE.match(line)
        if heading:
            body = "<b>%s</b>" % _inline(heading.group("text"))
        elif line.strip() and UNDERLINE.match(following):
            body = "<b>%s</b>" % _inline(line)
        else:
            body = _inline(line)
        if signature:
            body = '<span class="dim">%s</span>' % body
        elif line.startswith(">"):
            body = '<span class="quote">%s</span>' % body
        rendered.append((not line.strip(), body))

    # Blank runs collapse to one empty line, but only after the underline rows
    # are gone, which is why this works on the rendered list and not the text.
    out, blank = [], 0
    for empty, body in rendered:
        blank = blank + 1 if empty else 0
        if blank <= 1:
            out.append((empty, body))
    while out and out[0][0]:
        out.pop(0)
    while out and out[-1][0]:
        out.pop()
    return "<br>".join(body for _, body in out)
