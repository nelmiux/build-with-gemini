"""The docs page (docs.html) and the Markdown documents it shows: find their prose, and write edits back as
Markdown. Code blocks, diagrams, inline code, links' addresses, and raw HTML are never changed."""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, replace

from .htmlfrag import Fragment, find_units, has_markup, number_locks
from .jslex import annotate, lex, walk
from .model import Occurrence, Seg, has_words, merge, normalize
from .pages import Piece, _f, _frames, _skeleton, _str_value_segs, _template_text_piece, _tpl_fragment, _str_fragment, element_kind, _sr_only

DOCS_PAGE = 'docs.html'


def doc_list(src):
    """The documents in the viewer's order, with their titles: read from the DOCS list in docs.html."""
    m = re.search(r'<script>(.*?)</script>', src, re.S)
    toks, _ = lex(src, m.start(1), m.end(1))
    annotate(toks)
    items, cur = [], {}
    for t in walk(toks):
        if t.kind == 'str' and t.path and t.path[0][1] == 'DOCS' and not t.is_key:
            fr = _frames(t)
            key = _f(fr, -1)[1] if fr else None
            if len(fr) >= 4 and fr[-1][0] == '{':
                cur.setdefault(fr[-1][3], {})[fr[-1][1]] = t.value
    for fid, d in cur.items():
        if 'path' in d:
            items.append(d)
    return items


def _docs_files():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    src = (root / DOCS_PAGE).read_text(encoding='utf-8')
    return [DOCS_PAGE] + [d['path'] for d in doc_list(src)], {d['path']: d.get('title', d['path']) for d in doc_list(src)}


DOCS_FILES, DOC_TITLES = _docs_files()


DRAFTS = ('EXECUTIVE_SUMMARY.md', 'ASSESSMENT_AND_RECOMMENDATIONS.md')


def section_rank(path, section):
    return 2 if path == DOCS_PAGE else 1 if path in DRAFTS else 0


def section_title(path, section):
    if path == DOCS_PAGE:
        return 'The documentation page: menus, buttons, and messages'
    return '%s (%s)' % (DOC_TITLES.get(path, path), path)


def section_intro(path, section, home):
    if path == DOCS_PAGE:
        return 'Short labels and messages around every document. On the page: %sdocs.html' % home
    intro = 'On the page: %sdocs.html?doc=%s' % (home, path)
    if path in DRAFTS:
        intro = ('An earlier draft, kept for reference and superseded by the report. Proofread only; please do not '
                 'rewrite it. ' + intro)
    return intro


def md_link_target(href, path, home):
    if href.startswith('#'):
        return '%sdocs.html?doc=%s%s' % (home, path, href)
    base = path.rsplit('/', 1)[0] + '/' if '/' in path else ''
    target = href.split('#')[0]
    frag = ('#' + href.split('#', 1)[1]) if '#' in href else ''
    full = (base + target) if not target.startswith('/') else target.lstrip('/')
    parts = []
    for seg in full.split('/'):
        if seg == '..':
            if parts:
                parts.pop()
        elif seg and seg != '.':
            parts.append(seg)
    full = '/'.join(parts)
    if full.endswith('.md'):
        return '%sdocs.html?doc=%s%s' % (home, full, frag)
    return home + full + frag


# ------------------------------------------------------------------ docs.html

# Interface messages on the docs page, by (function, where the text goes): see pages.ui_message().
DOCS_UI = {
    ('postProcess', 'set:btn.textContent'): 'Button on code blocks (also what it says after copying)',
    ('setFit', 'set:btn.textContent?0'): 'Diagram button',
    ('setFit', 'set:btn.textContent?1'): 'Diagram button',
    ('setFit', 'set:btn.title?0'): 'Tooltip on a diagram button',
    ('setFit', 'set:btn.title?1'): 'Tooltip on a diagram button',
    ('setFit', 'set:hint.textContent'): 'Hint under a wide diagram',
    ('addDiagramToolbar', 'call:mkBtn#0'): 'Diagram button',
    ('addDiagramToolbar', 'call:mkBtn#1'): 'Tooltip on a diagram button',
    ('addDiagramToolbar', 'set:source.textContent?0'): 'Diagram button, while the source is shown',
    ('addDiagramToolbar', 'set:source.textContent?1'): 'Diagram button',
    ('renderDiagrams', 'call:showDiagramFallback#1'): 'Message in place of a diagram',
    ('updatePager', 'call:link#2'): 'Button at the end of each document',
    ('loadDoc', 'call:showError#0'): 'Title of an error message',
    ('loadDoc', 'call:updateToolbar#1'): 'Title shown when a document is not found',
    ('loadDoc', 'var:title?0'): 'Title of an error message',
    ('loadDoc', 'var:title?1'): 'Title of an error message ({1} is the file name)',
}


DOCS_TEMPLATE_WHERE = {   # by the first two words the writer sees
    'The {1}': 'Error message when a script library cannot load ({1} is \u201cMarkdown\u201d or \u201cHTML sanitizer\u201d; {2} is a link to the file)',
    'The requested': 'Error message for a document that is not on the site ({1} is the name that was asked for)',
    'The request': 'Error message when a document cannot be loaded ({1} is the error)',
    'You can': 'Line under an error message ({1} is a link to the file on GitHub)',
    'Diagram {1}': 'Label above each diagram ({1} is its number)',
}


def docs_page_pieces(src, path=DOCS_PAGE):
    pieces = []
    for f in find_units(Fragment(src), skip_inside=lambda t: t.attr('id') == 'dialog-title'):
        kind = element_kind(f)
        if _sr_only(f) and not f.attr:
            kind += ' (read aloud by screen readers only)'
        occ = Occurrence(path, f.start, f.end, 'attr' if f.attr else 'html', quote=f.quote, links=f.links, locks=f.locks,
                         tags=dict(f.tags, attr_quote=f.quote) if f.attr else f.tags,
                         rich=not f.attr and not any(n == 'title' for n, _ in f.stack),
                         where=kind[0].upper() + kind[1:], section='page', group='Page frame', order=(0, f.start, 0, 0))
        pieces.append(Piece(f.segs, occ))
    m = re.search(r'<script>(.*?)</script>', src, re.S)
    toks, _ = lex(src, m.start(1), m.end(1))
    annotate(toks)
    for t in walk(toks):
        if t.kind not in ('str', 'tpl') or not t.path:
            continue
        const = t.path[0][1]
        fr = _frames(t)
        if const == 'DOCS' and t.kind == 'str' and not t.is_key:
            key = fr[-1][1] if fr else None
            if key in ('group', 'title', 'blurb'):
                what = {'group': 'Group heading in the list of documents', 'title': 'Document title in the list of documents',
                        'blurb': 'One-line description in the list of documents'}[key]
                occ = Occurrence(path, t.start + 1, t.end - 1, 'js', quote=src[t.start], rich=False, where=what,
                                 section='page', group='List of documents', order=(1, t.start, 0, 0))
                pieces.append(Piece(_str_value_segs(t), occ))
            continue
        calls = [x for x in fr if x[0] == '(']
        if calls and calls[-1][2] == 'replace':
            continue
        sk = _skeleton(src, t)
        if has_markup(sk):
            frag = _tpl_fragment(src, t) if t.kind == 'tpl' else _str_fragment(src, t)
            for f in find_units(frag):
                kind = element_kind(f)
                shown = ''.join(x.text for x in f.segs)
                where = DOCS_TEMPLATE_WHERE.get(shown.split(' ', 2)[0] + ' ' + shown.split(' ', 2)[1] if ' ' in shown else shown)
                occ = Occurrence(path, f.start, f.end, 'jsattr' if f.attr else 'jshtml', quote=src[t.start], links=f.links,
                                 locks=f.locks, tags=dict(f.tags, attr_quote=f.quote) if f.attr else f.tags, rich=not f.attr,
                                 where=where or kind[0].upper() + kind[1:], section='page',
                                 group='Buttons and messages', order=(2, t.start, f.start, 0))
                pieces.append(Piece(f.segs, occ))
            continue
        continue
    # interface messages, found by where the script puts them
    from .pages import token_lists, ui_message
    for L in token_lists(toks):
        for i, t in enumerate(L):
            if t.kind not in ('str', 'tpl') or not t.path or t.path[0][1] == 'DOCS':
                continue
            if has_markup(_skeleton(src, t)):
                continue
            func = t.path[0][1]
            if func == 'SITE_TITLE' and t.kind == 'str':
                sig, where = 'const:SITE_TITLE', 'Browser tab title, after the document\u2019s name'
            else:
                sig = ui_message(L, i, func)
                if sig is None:
                    continue
                where = DOCS_UI.get((func, sig), 'Text shown by the page\u2019s script')
            if t.kind == 'str':
                segs = _str_value_segs(t)
                if not has_words(segs):
                    continue
                occ = Occurrence(path, t.start + 1, t.end - 1, 'js', quote=src[t.start], rich=False, where=where,
                                 section='page', group='Buttons and messages', order=(2, t.start, 0, 0), tags={'sink': sig})
            else:
                segs, locks, a, b = _template_text_piece(src, t)
                if not has_words(segs):
                    continue
                occ = Occurrence(path, a, b, 'jstpl', quote='`', locks=locks, rich=False, where=where, section='page',
                                 group='Buttons and messages', order=(2, t.start, 0, 0), tags={'sink': sig})
            pieces.append(Piece(segs, occ))
    return pieces


# ------------------------------------------------------------------ Markdown: blocks

FENCE_RE = re.compile(r'^([ \t]*)(`{3,}|~{3,})')   # any indentation: fences inside list items count too
ATX_RE = re.compile(r'^ {0,3}(#{1,6})(?:[ \t]+|$)')
HR_RE = re.compile(r'^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$')
SETEXT_RE = re.compile(r'^ {0,3}(?:=+|-+)[ \t]*$')
LIST_RE = re.compile(r'^( {0,3}(?:[-*+]|\d{1,9}[.)]))([ \t]+|$)')
TABLE_SEP_RE = re.compile(r'^ {0,3}\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$')
BLOCK_TAGS = ('address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|'
              'div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|'
              'li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|'
              'tfoot|th|thead|title|tr|track|ul|script|pre|style|textarea')
HTML_BLOCK_RE = re.compile(r'^ {0,3}(?:<(?:/?(?:%s)(?:[\s/>]|$))|<!--|<[A-Za-z][A-Za-z0-9-]*(?:\s[^>]*)?>\s*$)' % BLOCK_TAGS, re.I)
REFDEF_RE = re.compile(r'^ {0,3}\[[^\]]+\]:\s')


@dataclass
class Block:
    kind: str          # heading | para | item | quote | cell
    start: int         # source span of the content
    end: int
    prefix: str = ''   # for multi-line content: what each continuation line starts with ('> ' in quotes)
    label: str = ''    # human description


def _lines(text):
    out, pos = [], 0
    for line in text.split('\n'):
        out.append((pos, line))
        pos += len(line) + 1
    return out


def _starts_block(line):
    return bool(ATX_RE.match(line) or FENCE_RE.match(line) or HR_RE.match(line) or LIST_RE.match(line)
                or line.lstrip().startswith('>') or line.lstrip().startswith('|') or HTML_BLOCK_RE.match(line))


def md_blocks(text):
    lines = _lines(text)
    n = len(lines)
    i = 0
    blocks = []
    counts = {}
    heading = ''

    def label(kind):
        counts[(heading, kind)] = counts.get((heading, kind), 0) + 1
        k = '%s %d' % (kind, counts[(heading, kind)])
        return ('Under “%s” · %s' % (heading, k)) if heading else k[0].upper() + k[1:]

    while i < n:
        pos, line = lines[i]
        fm = FENCE_RE.match(line)
        if fm:
            fence = fm.group(2)
            i += 1
            while i < n and not re.match(r'^[ \t]*' + re.escape(fence[0]) + '{%d,}[ \t]*$' % len(fence), lines[i][1]):
                i += 1
            i += 1
            continue
        if line.lstrip().startswith('<!--'):
            while i < n and '-->' not in lines[i][1]:
                i += 1
            i += 1
            continue
        if not line.strip():
            i += 1; continue
        hm = ATX_RE.match(line)
        if hm:
            a = pos + hm.end()
            content = line[hm.end():]
            content = re.sub(r'[ \t]+#+[ \t]*$', '', content).rstrip()
            if content.strip():
                b = a + len(content)
                blocks.append(Block('heading', a, b, label='Heading'))
                heading = _plain_md(content)
            i += 1
            continue
        if HR_RE.match(line) or REFDEF_RE.match(line):
            i += 1; continue
        if HTML_BLOCK_RE.match(line):
            while i < n and lines[i][1].strip():
                i += 1
            continue
        # table: a row with pipes followed by a separator row
        if '|' in line and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1][1]):
            r = 0
            header = True
            columns = len(_cells(line))
            while i < n and '|' in lines[i][1] and lines[i][1].strip():
                lpos, l = lines[i]
                if TABLE_SEP_RE.match(l):
                    i += 1; header = False; continue
                r += 1
                for c, (a, b) in enumerate(_cells(l)):
                    if c >= columns:
                        break   # extra cells are not shown
                    if b > a:
                        blocks.append(Block('cell', lpos + a, lpos + b, label='%s · %s' % (
                            'Table under “%s”' % heading if heading else 'Table',
                            ('column heading %d' % (c + 1)) if header else 'row %d, column %d' % (r - 1, c + 1))))
                i += 1
            continue
        if line.lstrip().startswith('>'):
            # a block quote: each paragraph inside is one piece; continuation lines keep their '>'
            j = i
            while j < n and lines[j][1].lstrip().startswith('>'):
                j += 1
            k = i
            while k < j:
                inner = re.sub(r'^\s*>\s?', '', lines[k][1])
                if not inner.strip() or re.match(r'^\s*\[![A-Za-z]+\]\s*$', inner):
                    k += 1; continue   # blank, or a GitHub alert marker such as [!NOTE], which the viewer needs as it is
                qf = FENCE_RE.match(inner)
                if qf:   # a code block inside the quote: skip it
                    k += 1
                    while k < j and not re.match(r'^[ \t]*' + re.escape(qf.group(2)[0]) + '{3,}[ \t]*$', re.sub(r'^\s*>\s?', '', lines[k][1])):
                        k += 1
                    k += 1
                    continue
                m0 = re.match(r'^\s*>\s?', lines[k][1])
                a = lines[k][0] + m0.end()
                lm = LIST_RE.match(inner)
                if lm:
                    a += lm.end()
                last = k
                k += 1
                while k < j:
                    nxt = re.sub(r'^\s*>\s?', '', lines[k][1])
                    if not nxt.strip() or LIST_RE.match(nxt) or ATX_RE.match(nxt) or FENCE_RE.match(nxt):
                        break
                    last = k; k += 1
                b = lines[last][0] + len(lines[last][1].rstrip())
                blocks.append(Block('quote', a, b, prefix='> ', label=label('note')))
            i = j
            continue
        lm = LIST_RE.match(line)
        if lm:
            a = pos + lm.end()
            indent = len(lm.group(1)) + len(lm.group(2)) if lm.group(2) else len(lm.group(1)) + 1
            last = i
            i += 1
            while i < n:
                l = lines[i][1]
                if not l.strip() or LIST_RE.match(l) or FENCE_RE.match(l) or (_starts_block(l) and not l.startswith(' ' * indent)):
                    break
                if LIST_RE.match(l.lstrip()) and len(l) - len(l.lstrip()) >= 2:
                    break
                last = i; i += 1
            b = lines[last][0] + len(lines[last][1].rstrip())
            if b > a and line[lm.end():].strip():
                blocks.append(Block('item', a, b, label=label('list item')))
            continue
        # a paragraph (or a setext heading)
        a = pos + (len(line) - len(line.lstrip()))
        last = i
        i += 1
        setext = False
        while i < n:
            if _hard_break(lines[last][1]):
                break   # the next line starts a new piece
            l = lines[i][1]
            if SETEXT_RE.match(l) and l.strip():
                setext = True
                i += 1
                break
            if not l.strip() or _starts_block(l):
                break
            last = i; i += 1
        b = lines[last][0] + len(_strip_break(lines[last][1]))
        if setext:
            blocks.append(Block('heading', a, b, label='Heading'))
            heading = _plain_md(text[a:b])
        else:
            blocks.append(Block('para', a, b, label=label('paragraph')))
    return blocks


def _hard_break(line):
    return line.endswith('  ') or (line.endswith('\\') and not line.endswith('\\\\'))


def _strip_break(line):
    """The line without trailing spaces, and without a trailing backslash that marks a line break."""
    t = line.rstrip()
    if t.endswith('\\') and not t.endswith('\\\\') and line.rstrip() == line:
        t = t[:-1].rstrip()
    return t


def _cells(line):
    """Spans (relative to the line) of each table cell's content, trimmed."""
    spans, i, n = [], 0, len(line)
    s = line
    # skip leading whitespace and the leading pipe
    while i < n and s[i] in ' \t':
        i += 1
    if i < n and s[i] == '|':
        i += 1
    start = i
    while i <= n:
        if i == n or (s[i] == '|' and (i == 0 or s[i - 1] != '\\')):
            a, b = start, i
            while a < b and s[a] in ' \t':
                a += 1
            while b > a and s[b - 1] in ' \t':
                b -= 1
            if not (i == n and start == n):
                spans.append((a, b))
            start = i + 1
        i += 1
    # a trailing pipe leaves an empty last cell: drop it
    if spans and spans[-1][0] == spans[-1][1] and line.rstrip().endswith('|'):
        spans.pop()
    return spans


def _plain_md(s):
    return re.sub(r'[*_`]', '', re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', s)).strip()


# ------------------------------------------------------------------ Markdown: inline

URL_RE = re.compile(r'(?:https?://|www\.)[^\s<>`]*[^\s<>`.,:;"\')\]*_~?!]')
PUNCT = set('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~') | {c for c in '“”‘’—–…·→←'}
ESCAPABLE = set('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')


def _is_ws(c):
    return c == '' or c.isspace()


def _is_punct(c):
    return c != '' and (c in PUNCT or unicodedata.category(c).startswith('P'))


class _Text:
    def __init__(self, text):
        self.text = text


class _Delim:
    def __init__(self, ch, n, can_open, can_close):
        self.ch, self.n, self.orig, self.can_open, self.can_close = ch, n, n, can_open, can_close
        self.active = True


class _Lock:
    def __init__(self, src, text, kind):
        self.src, self.text, self.kind = src, text, kind


class _Fmt:
    def __init__(self, kind, opening):
        self.kind, self.opening = kind, opening


class _Link:
    def __init__(self, children, url, title, src):
        self.children, self.url, self.title, self.src = children, url, title, src


def _content_and_map(src, a, b, prefix_re):
    """The content of a multi-line block without its continuation prefixes ('> ' or indentation), with each
    kept character's source offset."""
    text, offs = [], []
    i = a
    line_start = False
    while i < b:
        ch = src[i]
        if ch == '\n':
            text.append(' ' if True else ch); offs.append(i)
            i += 1
            m = prefix_re.match(src, i)
            if m and m.end() <= b:
                i = m.end()
            continue
        text.append(ch); offs.append(i)
        i += 1
    return ''.join(text), offs


def parse_inline(s):
    """Parse Markdown inline content into nodes: text, emphasis delimiters, locked spans (code, autolinks, HTML,
    images), and links."""
    nodes = []
    i, n = 0, len(s)
    buf = []

    def flush():
        if buf:
            nodes.append(_Text(''.join(buf))); buf.clear()

    while i < n:
        c = s[i]
        if c == '\\' and i + 1 < n and s[i + 1] in ESCAPABLE:
            buf.append(s[i + 1]); i += 2; continue
        if c == '`':
            m = re.match(r'`+', s[i:])
            ticks = m.group(0)
            close = re.search(r'(?<!`)' + ticks + r'(?!`)', s[i + len(ticks):])
            if close:
                end = i + len(ticks) + close.end()
                inner = s[i + len(ticks):i + len(ticks) + close.start()]
                shown = inner[1:-1] if len(inner) >= 2 and inner[0] == ' ' and inner[-1] == ' ' and inner.strip() else inner
                flush(); nodes.append(_Lock(s[i:end], shown, 'code')); i = end; continue
            buf.append(ticks); i += len(ticks); continue
        if c == '<':
            m = re.match(r'<(?:[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*|[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+)>', s[i:])
            if m:
                flush(); nodes.append(_Lock(m.group(0), m.group(0)[1:-1], 'autolink')); i += m.end(); continue
            m = re.match(r'<(/?)(strong|b|em|i)>', s[i:])
            if m:
                flush(); nodes.append(_Fmt('bold' if m.group(2) in ('strong', 'b') else 'italic', m.group(1) != '/')); i += m.end(); continue
            m = re.match(r'</?[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>]*?)?\s*/?>|<!--.*?-->', s[i:], re.S)
            if m:
                flush(); nodes.append(_Lock(m.group(0), m.group(0), 'html')); i += m.end(); continue
        if c in 'hw' and (i == 0 or not s[i - 1].isalnum()):
            m = URL_RE.match(s, i)
            if m:
                flush(); nodes.append(_Lock(m.group(0), m.group(0), 'url')); i = m.end(); continue
        if c == '&':
            m = re.match(r'&(?:#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});', s[i:])
            if m:
                buf.append(html.unescape(m.group(0))); i += m.end(); continue
        if c == '!' and i + 1 < n and s[i + 1] == '[':
            end = _link_end(s, i + 1)
            if end:
                flush(); alt = s[i + 2:end[0]]
                nodes.append(_Lock(s[i:end[1]], '[image: %s]' % _plain_md(alt), 'image')); i = end[1]; continue
        if c == '[':
            end = _link_end(s, i)
            if end:
                inner = s[i + 1:end[0]]
                if '![' in inner:
                    flush(); nodes.append(_Lock(s[i:end[1]], _plain_md(inner) or 'link', 'image')); i = end[1]; continue
                flush()
                nodes.append(_Link(parse_inline(inner), end[2], end[3], s[i:end[1]]))
                i = end[1]; continue
        if c in '*_':
            m = re.match(r'\*+' if c == '*' else r'_+', s[i:])
            run = m.group(0)
            before = s[i - 1] if i > 0 else ''
            after = s[i + len(run)] if i + len(run) < n else ''
            left = not _is_ws(after) and (not _is_punct(after) or _is_ws(before) or _is_punct(before))
            right = not _is_ws(before) and (not _is_punct(before) or _is_ws(after) or _is_punct(after))
            if c == '*':
                can_open, can_close = left, right
            else:
                can_open = left and (not right or _is_punct(before))
                can_close = right and (not left or _is_punct(after))
            flush()
            nodes.append(_Delim(c, len(run), can_open, can_close))
            i += len(run); continue
        buf.append(c); i += 1
    flush()
    return _emphasis(nodes)


def _link_end(s, i):
    """For '[' at i: (index of the matching ']', end of the link, url, title), or None."""
    depth, j, n = 0, i, len(s)
    while j < n:
        c = s[j]
        if c == '\\':
            j += 2; continue
        if c == '`':
            m = re.match(r'`+', s[j:])
            close = s.find(m.group(0), j + len(m.group(0)))
            j = (close + len(m.group(0))) if close >= 0 else j + len(m.group(0)); continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                break
        j += 1
    else:
        return None
    if j + 1 < n and s[j + 1] == '(':
        m = re.match(r'\(\s*(<[^>]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)(?:\s+("[^"]*"|\'[^\']*\'|\([^)]*\)))?\s*\)', s[j + 1:])
        if m:
            url = m.group(1)
            if url.startswith('<'):
                url = url[1:-1]
            title = m.group(2)[1:-1] if m.group(2) else None
            return (j, j + 1 + m.end(), url, title)
    return None


def _emphasis(nodes):
    """CommonMark's delimiter algorithm: pair * and _ runs into emphasis and strong emphasis."""
    # each delimiter keeps its position in nodes; wrap matched spans in ('em'|'strong', children) markers
    out = list(nodes)
    i = 0
    while i < len(out):
        closer = out[i]
        if not (isinstance(closer, _Delim) and closer.can_close and closer.n > 0):
            i += 1; continue
        j = i - 1
        found = None
        while j >= 0:
            opener = out[j]
            if isinstance(opener, _Delim) and opener.ch == closer.ch and opener.can_open and opener.n > 0:
                odd = (opener.can_close or closer.can_open) and (opener.orig + closer.orig) % 3 == 0 \
                      and not (opener.orig % 3 == 0 and closer.orig % 3 == 0)
                if not odd:
                    found = j; break
            j -= 1
        if found is None:
            i += 1; continue
        opener = out[found]
        use = 2 if opener.n >= 2 and closer.n >= 2 else 1
        opener.n -= use; closer.n -= use
        inner = out[found + 1:i]
        wrapped = ('strong' if use == 2 else 'em', inner)
        out[found + 1:i] = [wrapped]
        i = found + 2
        if opener.n == 0:
            out.pop(found); i -= 1
        if closer.n == 0:
            out.pop(i)
    return out


def _to_segs(nodes, segs, locks, links, bold=False, italic=False, link=-1):
    tag_bold = tag_italic = 0
    base_bold, base_italic = bold, italic
    for nd in nodes:
        if isinstance(nd, _Fmt):
            if nd.kind == 'bold':
                tag_bold += 1 if nd.opening else -1
            else:
                tag_italic += 1 if nd.opening else -1
            continue
        bold, italic = base_bold or tag_bold > 0, base_italic or tag_italic > 0
        if isinstance(nd, _Text):
            segs.append(Seg(nd.text, bold, italic, False, link))
        elif isinstance(nd, _Delim):
            if nd.n:
                segs.append(Seg(nd.ch * nd.n, bold, italic, False, link))
        elif isinstance(nd, _Lock):
            shown = ' '.join(nd.text.split()) or nd.text
            locks.append({'src': nd.src, 'text': shown, 'kind': nd.kind})
            segs.append(Seg(shown, bold, italic, nd.kind == 'code', link, len(locks) - 1))
        elif isinstance(nd, _Link):
            links.append({'href': nd.url, 'title': nd.title, 'bold_outside': bold, 'italic_outside': italic})
            _to_segs(nd.children, segs, locks, links, bold, italic, len(links) - 1)
        elif isinstance(nd, tuple):
            kind, inner = nd
            _to_segs(inner, segs, locks, links, bold or kind == 'strong', italic or kind == 'em', link)


def md_unit(src, block):
    prefix_re = re.compile(r'[ \t]*>[ \t]?' if block.kind == 'quote' else r'[ \t]*')
    content, _ = _content_and_map(src, block.start, block.end, prefix_re)
    if block.kind == 'cell':
        content = content.replace('\\|', '|') if False else content
    segs, locks, links = [], [], []
    _to_segs(parse_inline(content), segs, locks, links)
    segs = normalize(segs)
    return segs, locks, links


def reread_md(written, kind):
    """What the tool will read back from one piece of Markdown it has just written."""
    segs, locks, links = [], [], []
    _to_segs(parse_inline(written), segs, locks, links)
    return normalize(segs)


def md_pieces(path, src):
    pieces = []
    for blk in md_blocks(src):
        segs, locks, links = md_unit(src, blk)
        if not has_words(segs):
            continue
        occ = Occurrence(path, blk.start, blk.end, 'md', quote=blk.kind, links=links, locks=locks, rich=True,
                         where=blk.label, section='doc', group='', order=(0, blk.start, 0, 0))
        pieces.append(Piece(segs, occ))
    return pieces


def pieces(path, text):
    if path == DOCS_PAGE:
        return docs_page_pieces(text, path)
    return md_pieces(path, text)


# ------------------------------------------------------------------ Markdown: writing back

def _md_escape(text, in_cell, at_start):
    # web addresses the writer typed go in as they are (escaping would put backslashes into the link)
    pieces, pos = [], 0
    for m in URL_RE.finditer(text):
        if m.start() > 0 and text[m.start() - 1].isalnum():
            continue
        pieces.append(_md_escape_plain(text[pos:m.start()], in_cell, at_start and pos == 0))
        pieces.append(m.group(0).replace('|', '\\|') if in_cell else m.group(0))
        pos = m.end()
    pieces.append(_md_escape_plain(text[pos:], in_cell, at_start and pos == 0))
    return ''.join(pieces)


def _md_escape_plain(text, in_cell, at_start):
    out = []
    for i, c in enumerate(text):
        prev = text[i - 1] if i else ''
        nxt = text[i + 1] if i + 1 < len(text) else ''
        if c in '\\`*[]~':
            out.append('\\' + c)
        elif c == '_' and not (prev.isalnum() and nxt.isalnum()):
            out.append('\\_')
        elif c == '<' and (nxt.isalpha() or nxt in '/!?'):
            out.append('\\<')
        elif c == '&' and re.match(r'&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);', text[i:]):
            out.append('&amp;')
        elif c == '|' and in_cell:
            out.append('\\|')
        else:
            out.append(c)
    s = ''.join(out)
    if at_start:
        s = re.sub(r'^([#>+]|-(?=\s)|=)', r'\\\1', s)
        s = re.sub(r'^(\d+)([.)])(?=\s)', r'\1\\\2', s)
    return s


def encode_md(segs, occ, source):
    """The edited segments as Markdown, reusing the original source of every locked part and link address.
    Emphasis uses ** and * wherever CommonMark will read them back given the characters on each side;
    otherwise it falls back to <strong> and <em>, which the docs page renders the same way."""
    in_cell = occ.quote == 'cell'
    toks = []          # ('text', s) | ('raw', s) | ('open', kind) | ('close', kind)

    def group(items, attr):
        res, cur = [], None
        for s_ in items:
            k = getattr(s_, attr)
            if cur is None or cur[0] != k:
                cur = (k, []); res.append(cur)
            cur[1].append(s_)
        return res

    def leaf(items):
        for s_ in items:
            if s_.lock >= 0:
                toks.append(('raw', occ.locks[s_.lock]['src']))
            elif s_.code:
                t = s_.text
                ticks = '`' * (max((len(m) for m in re.findall(r'`+', t)), default=0) + 1)
                pad = ' ' if t.startswith('`') or t.endswith('`') else ''
                toks.append(('raw', ticks + pad + t + pad + ticks))
            else:
                toks.append(('text', s_.text))

    def wrapped(items, kind):
        items = list(items)
        lead = trail = ''
        if items and items[0].lock < 0 and not items[0].code:
            t = items[0].text
            lead = t[:len(t) - len(t.lstrip(' '))]
            items[0] = replace(items[0], text=t.lstrip(' '))
        if items and items[-1].lock < 0 and not items[-1].code:
            t = items[-1].text
            trail = t[len(t.rstrip(' ')):]
            items[-1] = replace(items[-1], text=t.rstrip(' '))
        items = [x for x in items if x.text or x.lock >= 0]
        if lead:
            toks.append(('text', lead))
        if items:
            toks.append(('open', kind))
            if kind == 'strong':
                for it, part in group(items, 'italic'):
                    wrapped(part, 'em') if it else leaf(part)
            else:
                leaf(items)
            toks.append(('close', kind))
        if trail:
            toks.append(('text', trail))

    def emph(items):
        for b, part in group(items, 'bold'):
            if b:
                wrapped(part, 'strong')
            else:
                for it, sub in group(part, 'italic'):
                    wrapped(sub, 'em') if it else leaf(sub)

    for k, part in group(segs, 'link'):
        if k >= 0:
            ln = occ.links[k]
            title = ' "%s"' % ln['title'].replace('"', '\\"') if ln.get('title') else ''
            url = ln.get('href') or ''
            if re.search(r'[\s()<>]', url):
                url = '<%s>' % url
            outside = [kind for kind, flag in (('strong', 'bold'), ('em', 'italic'))
                       if ln.get(flag + '_outside') and all(getattr(x, flag) for x in part)]
            for kind in outside:
                toks.append(('open', kind))
            inner_part = [replace(x, bold=x.bold and 'strong' not in outside, italic=x.italic and 'em' not in outside) for x in part]
            toks.append(('raw', '['))
            emph(inner_part)
            toks.append(('raw', '](%s%s)' % (url, title)))
            for kind in reversed(outside):
                toks.append(('close', kind))
        else:
            emph(part)
    out = _finish(toks, in_cell)
    if occ.quote == 'heading' and out.endswith('#'):
        out = out[:-1] + '\\#'
    return out


MARK = {'strong': '**', 'em': '*'}
TAG = {'strong': ('<strong>', '</strong>'), 'em': ('<em>', '</em>')}


def _finish(toks, in_cell):
    """Escape the text, then choose each emphasis marker from the characters really next to it."""
    out = []
    for kind, val in toks:
        if kind == 'text':
            at_start = not any(o[0] in ('text', 'raw') and o[1] for o in out)
            out.append(['text', _md_escape(val, in_cell, at_start)])
        else:
            out.append([kind, val])
    stack, pairs = [], {}
    for i, (kind, val) in enumerate(out):
        if kind == 'open':
            stack.append(i)
        elif kind == 'close':
            pairs[stack.pop()] = i

    def piece(j):
        k, v = out[j]
        return v if k in ('text', 'raw') else MARK[v]

    def char_before(i):
        for j in range(i - 1, -1, -1):
            if piece(j):
                return piece(j)[-1]
        return ''

    def char_after(i):
        for j in range(i + 1, len(out)):
            if piece(j):
                return piece(j)[0]
        return ''

    def can_open(i):
        b, a = char_before(i), char_after(i)
        return not _is_ws(a) and (not _is_punct(a) or _is_ws(b) or _is_punct(b))

    def can_close(i):
        b, a = char_before(i), char_after(i)
        return not _is_ws(b) and (not _is_punct(b) or _is_ws(a) or _is_punct(a))

    use_tag = set()
    for o, c in pairs.items():
        if not (can_open(o) and can_close(c)):
            use_tag.update((o, c))
    res = []
    for i, (kind, val) in enumerate(out):
        if kind in ('text', 'raw'):
            res.append(val)
        else:
            res.append(TAG[val][0 if kind == 'open' else 1] if i in use_tag else MARK[val])
    return ''.join(res)
