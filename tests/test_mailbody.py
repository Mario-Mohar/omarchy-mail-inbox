"""Tests for the body rendering behind the reader view.

The panel shows bodyHtml as Qt rich text, so the property that matters most is
that nothing the mail wrote survives as markup: every tag in the output has to
be one mailbody.py put there. The rest is about readability, links that work,
and HTML-only mails keeping their link targets.
"""

import re

import mailbody
import mailcommon

TAG = re.compile(r"</?([a-z]+)\b[^>]*>")


def tags(rendered):
    return {m.group(1) for m in TAG.finditer(rendered)}


# ---- escaping ---------------------------------------------------------------

def test_markup_from_the_mail_is_escaped():
    out = mailbody.render_html('<img src="https://evil.example/t.png"> <b>hi</b> & bye')
    assert "<img" not in out
    assert out.startswith("&lt;img src=&quot;")
    assert "&lt;b&gt;hi&lt;/b&gt;" in out
    assert "&amp; bye" in out


def test_only_own_tags_appear():
    text = ("<script>x</script> [a](https://a.example) **b** https://c.example\n"
            "> quoted\n-- \nsig <i>i</i>")
    assert tags(mailbody.render_html(text)) <= {"a", "b", "br", "span"}


def test_link_target_cannot_break_out_of_the_attribute():
    out = mailbody.render_html('https://a.example/"onmouseover="x')
    assert 'onmouseover="' not in out


def test_other_schemes_are_not_linked():
    out = mailbody.render_html("[click](javascript:alert(1)) file:///etc/passwd")
    assert "<a" not in out


# ---- inline formatting ------------------------------------------------------

def test_markdown_link_uses_its_label():
    out = mailbody.render_html("see [the docs](https://docs.example/x) now")
    assert out == 'see <a href="https://docs.example/x">the docs</a> now'


def test_bare_url_is_linked_and_shortened():
    url = "https://example.com/" + "a" * 100
    out = mailbody.render_html("go: " + url)
    assert '<a href="%s">' % url in out
    assert "example.com/aaa" in out and "…</a>" in out
    assert "https://" not in out.split(">", 1)[1]


def test_trailing_full_stop_is_not_part_of_the_url():
    out = mailbody.render_html("Read https://example.com/page.")
    assert out == 'Read <a href="https://example.com/page">example.com/page</a>.'


def test_balanced_parenthesis_stays_in_the_url():
    out = mailbody.render_html("https://en.wikipedia.org/wiki/Mail_(Apple)")
    assert 'href="https://en.wikipedia.org/wiki/Mail_(Apple)"' in out


def test_angle_bracket_url():
    out = mailbody.render_html("<https://example.com/a>")
    assert out == '<a href="https://example.com/a">example.com/a</a>'


def test_bold_and_headings():
    out = mailbody.render_html("## Report\n**done** and more")
    assert out == "<b>Report</b><br><b>done</b> and more"


def test_setext_heading_drops_its_underline():
    out = mailbody.render_html("Title\n-----\nbody")
    assert out == "<b>Title</b><br>body"


def test_signature_and_quotes_are_marked():
    out = mailbody.render_html("hi\n> earlier\n-- \nMe")
    assert '<span class="quote">&gt; earlier</span>' in out
    assert '<span class="dim">Me</span>' in out


def test_blank_runs_collapse_and_edges_are_trimmed():
    out = mailbody.render_html("\n\n a\r\n\r\n\r\n\r\nb\n\n")
    assert out == " a<br><br>b"


def test_empty_body():
    assert mailbody.render_html("") == ""


# ---- HTML parts -------------------------------------------------------------

def test_html_links_keep_their_target():
    text = mailbody.html_to_text('<p>Click <a href="https://x.example/y?a=1&amp;b=2">here</a></p>')
    assert text == "Click [here](https://x.example/y?a=1&b=2)"


def test_html_link_with_url_as_label_is_not_doubled():
    text = mailbody.html_to_text('<a href="https://x.example">https://x.example</a>')
    assert text == "https://x.example"


def test_html_image_link_falls_back_to_target():
    text = mailbody.html_to_text('<a href="https://x.example"><img src="b.png"></a>')
    assert text == "https://x.example"


def test_html_unsafe_link_keeps_only_label():
    text = mailbody.html_to_text('<a href="javascript:void(0)">Open</a>')
    assert text == "Open"


def test_html_structure():
    raw = ("<html><head><style>p{}</style></head><body>"
           "<h1>News</h1><p>One\n  two</p><ul><li>a</li><li><b>b</b></li></ul>"
           "<!-- hidden --><script>bad()</script></body></html>")
    assert mailbody.html_to_text(raw) == "# News\n\nOne two\n\n• a\n• **b**"


def test_html_round_trip_renders_link():
    out = mailbody.render_html(mailbody.html_to_text('<a href="https://x.example/z">Go</a>'))
    assert out == '<a href="https://x.example/z">Go</a>'


# ---- the emit cap no longer eats long bodies --------------------------------

def test_long_body_survives_bound():
    payload = mailcommon._bound({"body": "x" * 5000, "subject": "y" * 5000})
    assert len(payload["body"]) == 5000
    assert len(payload["subject"]) == mailcommon.MAX_FIELD_VALUE
