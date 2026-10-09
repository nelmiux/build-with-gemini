"""Data model shared by the extractors, the document writer and reader, and the importer.

A *unit* is one piece of reader-visible text (a paragraph, a button label, a slide bullet). Its content is
a list of *segments*: runs of text with one set of formatting. Some segments are *locked*: text the writer
must not change, such as a value filled in by the page's script. A unit can appear in several places in
the sources (an *occurrence* each); an edit to the unit is written back to every occurrence.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import List


@dataclass
class Seg:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: int = -1   # index into Occurrence.links, or -1
    lock: int = -1   # index into Occurrence.locks, or -1; the text is fixed

    def style(self):
        return (self.bold, self.italic, self.code, self.link, self.lock)


@dataclass
class Occurrence:
    file: str              # path relative to the repository root
    start: int             # character offsets of the source text this unit was read from
    end: int
    ctx: str               # how the text is written in the source: html | attr | js | jshtml | jsattr | md
    quote: str = ''        # for JavaScript contexts: the enclosing quote character (' " or `)
    links: list = field(default_factory=list)   # html: {'open': '<a ...>'}; md: {'url': ..., 'title': ...}
    locks: list = field(default_factory=list)   # {'src': exact source text, 'text': what the writer sees, 'kind': ...}
    tags: dict = field(default_factory=dict)    # original opening tags for bold and italic, reused on write-back
    magic: list = field(default_factory=list)   # (phrase, link_text): plain-text phrases the page turns into links
    rich: bool = True      # False when the context cannot carry bold, italic or links
    where: str = ''        # human description, for the writer
    section: str = ''      # document section this belongs to
    group: str = ''        # sub-heading inside the section
    order: float = 0.0     # reading order
    note: str = ''         # extra guidance for the writer
    locked: bool = False   # shown for context only


@dataclass
class Unit:
    key: str
    segs: List[Seg]
    occs: List[Occurrence]

    @property
    def locked(self):
        return all(o.locked for o in self.occs)

    @property
    def text(self):
        return plain(self.segs)


WS = re.compile(r'[ \t\r\n\f]+')


def merge(segs):
    """Join neighbouring segments that share a style (locked segments stay separate)."""
    out = []
    for s in segs:
        if not s.text and s.lock < 0:
            continue
        if out and s.lock < 0 and out[-1].lock < 0 and out[-1].style() == s.style():
            out[-1] = replace(out[-1], text=out[-1].text + s.text)
        else:
            out.append(replace(s))
    return out


def normalize(segs):
    """Collapse each run of whitespace into one space, kept where the run began, and trim both ends,
    as a browser shows the text."""
    out = []
    prev_ws = True   # whitespace at the very start is dropped
    for s in segs:
        if s.lock >= 0:
            out.append(replace(s))
            prev_ws = False
            continue
        buf = []
        for ch in s.text:
            if ch in ' \t\r\n\f':
                if not prev_ws:
                    buf.append(' ')
                    prev_ws = True
            else:
                buf.append(ch)
                prev_ws = False
        if buf:
            out.append(replace(s, text=''.join(buf)))
    while out and out[-1].lock < 0 and out[-1].text.endswith(' '):
        t = out[-1].text.rstrip(' ')
        if t:
            out[-1] = replace(out[-1], text=t)
        else:
            out.pop()
    return merge(_spaces_outside(merge(out)))


def _spaces_outside(segs):
    """A space at the edge of bold, italic, code or a link belongs to the plain text next to it (Word selects
    a word with its space; Markdown cannot open or close emphasis next to a space)."""
    out = []
    for s in segs:
        styled = s.lock < 0 and (s.bold or s.italic or s.code or s.link >= 0)
        if not styled:
            out.append(s)
            continue
        text = s.text
        if text.startswith(' '):
            out.append(Seg(' '))
            text = text[1:]
        trail = text.endswith(' ')
        if trail:
            text = text[:-1]
        if text:
            out.append(replace(s, text=text))
        if trail:
            out.append(Seg(' '))
    return out


def plain(segs):
    return ''.join(s.text for s in segs)


def canon(segs):
    """A stable string form of the content and its formatting, used to compare and to fingerprint."""
    return json.dumps([[s.text, int(s.bold), int(s.italic), int(s.code), s.link, s.lock] for s in normalize(segs)],
                      ensure_ascii=False, separators=(',', ':'))


def fingerprint(segs):
    return hashlib.sha1(canon(segs).encode('utf-8')).hexdigest()[:8]


def has_words(segs):
    """True when the unlocked text contains at least one letter (pure numbers or symbols are not copy)."""
    return any(s.lock < 0 and re.search(r'[^\W\d_]', s.text) for s in segs)
