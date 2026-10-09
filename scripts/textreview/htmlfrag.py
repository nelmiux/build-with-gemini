"""HTML: find the reader-visible text in a page, or in an HTML fragment held in a JavaScript string, and
write edited text back with the original markup around it."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .model import Occurrence, Seg, has_words, merge, normalize

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
RAW_TEXT = {'script', 'style'}
PLAIN_TEXT = {'title', 'textarea'}   # their content is text, never markup
FORMAT = {'strong': 'bold', 'b': 'bold', 'em': 'italic', 'i': 'italic', 'code': 'code', 'a': 'link'}
COPY_ATTRS = {'title': 'tooltip', 'aria-label': 'screen-reader label', 'placeholder': 'hint shown inside the box',
              'alt': 'image description', 'aria-roledescription': 'screen-reader name for this kind of element',
              'data-label': 'column label shown on phones'}
SPOKEN_ATTRS = {'aria-label', 'aria-roledescription'}   # never heard inside aria-hidden="true"
PH_FIRST = 0xE000   # private-use characters stand in for ${...} expressions inside templates

TAG_RE = re.compile(r'<(/?)([A-Za-z][A-Za-z0-9-]*|[-])((?:[^>"\']|"[^"]*"|\'[^\']*\')*)>', re.S)
COMMENT_RE = re.compile(r'<!--.*?-->', re.S)
DECL_RE = re.compile(r'<![A-Za-z][^>]*>')
ATTR_RE = re.compile(r'''([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+)))?''')
HAS_TAG_RE = re.compile(r'</?[A-Za-z][A-Za-z0-9-]*(?:[\s>/]|$)')


def is_placeholder(ch):
    return PH_FIRST <= ord(ch) <= 0xF8FF


@dataclass
class Tag:
    start: int
    end: int
    name: str
    is_end: bool
    attrs: list            # [(name, value, value_start, value_end, quote)] in fragment coordinates
    self_closing: bool = False

    def attr(self, name, default=None):
        for a in self.attrs:
            if a[0] == name:
                return a[1]
        return default

    def classes(self):
        return (self.attr('class') or '').split()


@dataclass
class Text:
    start: int
    end: int


@dataclass
class Skip:
    start: int
    end: int


@dataclass
class Fragment:
    """Text to scan, with a map from each character back to the source span it came from."""
    text: str
    base: int = 0                                  # identity map with this offset when starts is None
    starts: Optional[List[int]] = None
    ends: Optional[List[int]] = None
    exprs: Dict[str, str] = field(default_factory=dict)   # placeholder character -> raw ${...} source

    def src_span(self, a, b):
        """Source span covering fragment characters a..b-1."""
        if self.starts is None:
            return self.base + a, self.base + b
        return self.starts[a], self.ends[b - 1]


def tokenize(text):
    toks, pos, n = [], 0, len(text)
    while pos < n:
        lt = text.find('<', pos)
        if lt < 0:
            toks.append(Text(pos, n)); break
        if lt > pos:
            toks.append(Text(pos, lt))
        m = COMMENT_RE.match(text, lt) or DECL_RE.match(text, lt)
        if m:
            toks.append(Skip(lt, m.end())); pos = m.end(); continue
        m = TAG_RE.match(text, lt)
        if not m:
            toks.append(Text(lt, lt + 1)); pos = lt + 1; continue
        attrs = []
        for am in ATTR_RE.finditer(m.group(3)):
            name = am.group(1).lower()
            if name == '/':
                continue
            for gi, q in ((2, '"'), (3, "'"), (4, '')):
                if am.group(gi) is not None:
                    vs, ve = m.start(3) + am.start(gi), m.start(3) + am.end(gi)
                    attrs.append((name, html.unescape(am.group(gi)), vs, ve, q))
                    break
            else:
                attrs.append((name, None, -1, -1, ''))
        tag = Tag(lt, m.end(), m.group(2).lower(), m.group(1) == '/', attrs, m.group(3).rstrip().endswith('/'))
        toks.append(tag)
        pos = m.end()
        if not tag.is_end and tag.name in RAW_TEXT | PLAIN_TEXT:
            close = re.compile(r'</' + tag.name + r'\s*>', re.I).search(text, pos)
            end = close.start() if close else n
            toks.append(Skip(pos, end) if tag.name in RAW_TEXT else Text(pos, end))
            pos = end
    # join neighbouring text tokens
    out = []
    for t in toks:
        if isinstance(t, Text) and out and isinstance(out[-1], Text) and out[-1].end == t.start:
            out[-1] = Text(out[-1].start, t.end)
        else:
            out.append(t)
    return out


def has_markup(s):
    return bool(HAS_TAG_RE.search(s))


def lock_span(tag):
    """Inline elements whose content is fixed: values the script fills in (span with an id) and product names."""
    return tag.name == 'span' and (tag.attr('id') is not None or 'pname' in tag.classes())


@dataclass
class Found:
    """A unit read from a fragment, before it is given a key."""
    segs: list
    start: int          # source span
    end: int
    links: list
    locks: list
    tags: dict
    stack: list         # ancestors: [(name, Tag)]
    attr: str = ''      # attribute name for attribute units
    quote: str = ''


def find_units(frag: Fragment, skip_inside: Callable = None, decode=True):
    """Every run of reader-visible text in the fragment: element text (with bold, italic, code, links and
    locked parts) and the copy attributes (tooltips, screen-reader labels, placeholders)."""
    text = frag.text
    toks = tokenize(text)
    found = []
    stack = []          # open elements: (name, Tag)
    run = []            # tokens of the current inline run
    inline_open = []    # names of formatting elements open in the run
    i = 0

    def flush():
        nonlocal run, inline_open
        if run and not inline_open:
            u = _build(frag, run, stack, decode)
            if u is not None:
                found.append(u)
        run, inline_open = [], []

    def skipping():
        # text in <pre> is code or a command, never prose
        return any(name == 'pre' for name, _ in stack) or (skip_inside is not None and any(skip_inside(t) for _, t in stack))

    while i < len(toks):
        t = toks[i]
        if isinstance(t, Skip):
            flush(); i += 1; continue
        if isinstance(t, Text):
            if not skipping():
                run.append(t)
            i += 1; continue
        # a tag
        if not t.is_end:
            for name, value, vs, ve, q in t.attrs:
                is_meta_desc = t.name == 'meta' and name == 'content' and (t.attr('name') or '').lower() == 'description'
                hidden = name in SPOKEN_ATTRS and any(tg.attr('aria-hidden') == 'true' for _, tg in stack + [(t.name, t)])
                if (name in COPY_ATTRS or is_meta_desc) and value and ve > vs and not skipping() and not hidden:
                    locks = []
                    segs = normalize(_text_segs(value, frag, locks))
                    if has_words(segs):
                        number_locks(segs, locks)
                        s0, e0 = frag.src_span(vs, ve)
                        found.append(Found(segs, s0, e0, [], locks, {}, list(stack) + [(t.name, t)],
                                           attr=('description' if is_meta_desc else name), quote=q))
        if t.name in FORMAT and not t.is_end:
            j = _matching_end(toks, i)
            inner_text = ''.join(text[x.start:x.end] for x in toks[i + 1:j] if isinstance(x, Text)) if j else ''
            if j is None or not inner_text.strip():
                flush()   # an empty <i></i> or <b></b> is a drawing (a colour swatch), not text
                i = (j + 1) if j is not None else i + 1
                continue
            if t.name == 'a' and not _in_running_text(text, toks, run, j):
                pass      # a link that stands alone (navigation, buttons): its text is a unit of its own
            else:
                if skipping():
                    i += 1; continue
                run.append(t); inline_open.append(t.name); i += 1; continue
        if t.name in FORMAT and t.is_end and not (t.name == 'a' and stack and stack[-1][0] == 'a'):
            if inline_open and inline_open[-1] == t.name:
                run.append(t); inline_open.pop()
            else:
                flush()
            i += 1; continue
        if t.name == 'span' and not t.is_end and lock_span(t):
            j = _matching_end(toks, i)
            if j is not None and not skipping():
                run.append(('lock', toks[i:j + 1]))
                i = j + 1; continue
        # any other tag ends the run
        flush()
        if t.is_end:
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] == t.name:
                    del stack[k:]
                    break
        elif t.name not in VOID and not t.self_closing:
            stack.append((t.name, t))
        i += 1
    flush()
    return found


def _in_running_text(text, toks, run, j):
    """Whether a link sits inside a sentence: there is text before it in the run, or right after it."""
    if any(isinstance(x, Text) and text[x.start:x.end].strip() for x in run) or any(isinstance(x, tuple) for x in run):
        return True
    for x in toks[j + 1:]:
        if isinstance(x, Text):
            if text[x.start:x.end].strip():
                return True
            continue
        if isinstance(x, Tag) and ((x.name in FORMAT and x.name != 'a') or lock_span(x)) and not x.is_end:
            return True
        return False
    return False


def _matching_end(toks, i):
    name, depth = toks[i].name, 0
    for j in range(i, len(toks)):
        t = toks[j]
        if isinstance(t, Tag) and t.name == name:
            if not t.is_end and not t.self_closing:
                depth += 1
            elif t.is_end:
                depth -= 1
                if depth == 0:
                    return j
    return None


def _text_segs(value, frag, locks, style=(False, False, False, -1)):
    """Split decoded text at ${...} placeholders, which become locked segments."""
    segs, buf = [], []
    for ch in value:
        if is_placeholder(ch):
            if buf:
                segs.append(Seg(''.join(buf), *style)); buf = []
            locks.append({'src': frag.exprs.get(ch, ''), 'text': '', 'kind': 'expr'})
            segs.append(Seg('', *style[:4], lock=len(locks) - 1))
        else:
            buf.append(ch)
    if buf:
        segs.append(Seg(''.join(buf), *style))
    return segs


def number_locks(segs, locks):
    """Placeholders for script values are shown to the writer as {1}, {2}... in order."""
    n = 0
    for s in segs:
        if s.lock >= 0 and locks[s.lock]['kind'] == 'expr':
            n += 1
            locks[s.lock]['text'] = '{%d}' % n
    for s in segs:
        if s.lock >= 0:
            s.text = locks[s.lock]['text']


def _build(frag, run, stack, decode):
    text = frag.text
    segs, links, locks, tags = [], [], [], {}
    bold = italic = code = 0
    link = -1
    for item in run:
        if isinstance(item, tuple) and item[0] == 'lock':
            toks = item[1]
            a, b = toks[0].start, toks[-1].end
            inner = ''.join(text[x.start:x.end] for x in toks[1:-1] if isinstance(x, Text))
            shown = html.unescape(inner) if decode else inner
            s0, e0 = frag.src_span(a, b)
            locks.append({'src': None, 'span': (s0, e0), 'text': ' '.join(shown.split()), 'kind': 'element'})
            segs.append(Seg('', bool(bold), bool(italic), bool(code), link, len(locks) - 1))
            continue
        if isinstance(item, Text):
            raw = text[item.start:item.end]
            value = html.unescape(raw) if decode else raw
            segs.extend(_text_segs(value, frag, locks, (bool(bold), bool(italic), bool(code), link)))
            continue
        t = item
        kind = FORMAT[t.name]
        s0, e0 = frag.src_span(t.start, t.end)
        if kind == 'bold':
            bold += -1 if t.is_end else 1
            if not t.is_end:
                tags.setdefault('bold', {'span': (s0, e0), 'name': t.name})
        elif kind == 'italic':
            italic += -1 if t.is_end else 1
            if not t.is_end:
                tags.setdefault('italic', {'span': (s0, e0), 'name': t.name})
        elif kind == 'code':
            code += -1 if t.is_end else 1
        elif kind == 'link':
            if t.is_end:
                link = -1
            else:
                links.append({'span': (s0, e0), 'href': t.attr('href') or ''})
                link = len(links) - 1
    # the span of the run, without the whitespace at either end
    first, last = run[0], run[-1]
    a = first[1][0].start if isinstance(first, tuple) else first.start
    b = last[1][-1].end if isinstance(last, tuple) else last.end
    if isinstance(first, Text):
        while a < b and text[a] in ' \t\r\n\f':
            a += 1
    if isinstance(last, Text):
        while b > a and text[b - 1] in ' \t\r\n\f':
            b -= 1
    if b <= a:
        return None
    segs = normalize(segs)
    if not has_words(segs):
        return None
    number_locks(segs, locks)
    s0, e0 = frag.src_span(a, b)
    return Found(segs, s0, e0, links, locks, tags, list(stack))


# ---------------------------------------------------------------- writing back

def esc_text(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def esc_attr(s, quote='"'):
    s = s.replace('&', '&amp;').replace('<', '&lt;')
    return s.replace('"', '&quot;') if quote != "'" else s.replace("'", '&#39;')


def close_tag(name):
    return '</%s>' % name


def encode(segs, occ, source):
    """The edited segments as a list of pieces: ('markup', html) to be escaped for the container, and
    ('raw', source) copied as it is (locked parts and original tags, already in source form)."""
    pieces = []

    def raw_of(span):
        return source[span[0]:span[1]]

    def emit_text(s):
        if s.lock >= 0:
            lk = occ.locks[s.lock]
            pieces.append(('raw', lk['src'] if lk.get('src') is not None else raw_of(lk['span'])))
        else:
            pieces.append(('markup', esc_text(s.text)))

    def group(items, attr):
        out, cur = [], None
        for s in items:
            k = getattr(s, attr)
            if cur is None or cur[0] != k:
                cur = (k, [])
                out.append(cur)
            cur[1].append(s)
        return out

    def emit_code(items):
        for flag, part in group(items, 'code'):
            if flag:
                pieces.append(('markup', '<code>'))
            for s in part:
                emit_text(s)
            if flag:
                pieces.append(('markup', '</code>'))

    def emit_wrapped(items, attr, kind, default, inner):
        for flag, part in group(items, attr):
            if flag:
                tag = occ.tags.get(kind)
                if tag and tag.get('span'):
                    pieces.append(('raw', raw_of(tag['span'])))
                    name = tag['name']
                else:
                    pieces.append(('markup', '<%s>' % default))
                    name = default
                inner(part)
                pieces.append(('markup', close_tag(name)))
            else:
                inner(part)

    def emit_italic(items):
        emit_wrapped(items, 'italic', 'italic', 'em', emit_code)

    def emit_bold(items):
        emit_wrapped(items, 'bold', 'bold', 'strong', emit_italic)

    for k, part in group(segs, 'link'):
        if k >= 0:
            ln = occ.links[k]
            if ln.get('span'):
                pieces.append(('raw', raw_of(ln['span'])))
            else:
                pieces.append(('markup', '<a href="%s" rel="noopener" target="_blank">' % esc_attr(ln['href'])))
            emit_bold(part)
            pieces.append(('markup', '</a>'))
        else:
            emit_bold(part)
    return pieces


def encode_plain(segs, occ, source, attr_quote=None):
    """Text without markup (attributes and plain JavaScript strings); locked parts stay as they are."""
    pieces = []
    for s in segs:
        if s.lock >= 0:
            lk = occ.locks[s.lock]
            pieces.append(('raw', lk['src'] if lk.get('src') is not None else source[lk['span'][0]:lk['span'][1]]))
        else:
            pieces.append(('markup', esc_attr(s.text, attr_quote) if attr_quote is not None else s.text))
    return pieces
