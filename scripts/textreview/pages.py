"""Find the reader-visible text in the two pages (index.html and docs.html): the HTML itself, the data the
page scripts render, the HTML templates in those scripts, and a short list of interface messages.
Every piece comes back as a Found-like record with an exact source span, so an edit can be written back."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .htmlfrag import Fragment, find_units, has_markup, number_locks, is_placeholder
from .jslex import annotate, js_unescape, lex, walk
from .model import Occurrence, Seg, has_words, normalize

PH = 0xE000


@dataclass
class Piece:
    segs: list
    occ: Occurrence


# ------------------------------------------------------------------ helpers

def _frames(tok):
    return tok.path[1:]


def _f(frames, i):
    return frames[i] if i < len(frames) else (None, None, None, None)


def _str_value_segs(tok):
    return normalize([Seg(tok.value)])


def _tpl_fragment(src, tok):
    """The template's text with a private-use character standing in for each ${...}."""
    text, starts, ends, exprs = [], [], [], {}
    k = 0
    for part in tok.parts:
        if part[0] == 'lit':
            raw = src[part[1]:part[2]]
            dec, s, e = js_unescape(raw)
            text.append(dec)
            starts.extend(part[1] + x for x in s)
            ends.extend(part[1] + x for x in e)
        else:
            ch = chr(PH + k); k += 1
            exprs[ch] = src[part[1]:part[2]]
            text.append(ch); starts.append(part[1]); ends.append(part[2])
    return Fragment(''.join(text), starts=starts, ends=ends, exprs=exprs)


def _str_fragment(src, tok):
    raw = src[tok.start + 1:tok.end - 1]
    dec, s, e = js_unescape(raw)
    base = tok.start + 1
    return Fragment(dec, starts=[base + x for x in s], ends=[base + x for x in e])


def _skeleton(src, tok):
    if tok.kind == 'str':
        return tok.value
    return ''.join(js_unescape(src[p[1]:p[2]])[0] if p[0] == 'lit' else '{}' for p in tok.parts)


def _template_text_piece(src, tok):
    """A template without markup, used as plain text: its literal parts are editable, the ${...} are locked."""
    frag = _tpl_fragment(src, tok)
    segs, locks, buf = [], [], []
    for ch in frag.text:
        if is_placeholder(ch):
            if buf:
                segs.append(Seg(''.join(buf))); buf = []
            locks.append({'src': frag.exprs[ch], 'text': '', 'kind': 'expr'})
            segs.append(Seg('', lock=len(locks) - 1))
        else:
            buf.append(ch)
    if buf:
        segs.append(Seg(''.join(buf)))
    segs = normalize(segs)
    number_locks(segs, locks)
    # span: the template's body without the backticks, trimmed of whitespace at the ends
    a, b = tok.start + 1, tok.end - 1
    while a < b and src[a] in ' \t\r\n':
        a += 1
    while b > a and src[b - 1] in ' \t\r\n':
        b -= 1
    return segs, locks, a, b


ELEMENT_WORDS = [
    ('title', 'browser tab title'), ('h1', 'title'), ('h2', 'heading'), ('h3', 'heading'), ('h4', 'heading'),
    ('p', 'paragraph'), ('li', 'list item'), ('summary', 'title of a section that opens and closes'),
    ('th', 'table column heading'), ('td', 'table cell'), ('dt', 'term'), ('dd', 'definition'),
    ('button', 'button'), ('label', 'label'), ('figcaption', 'caption'),
]
ATTR_WORDS = {'data-label': 'column label shown on phones', 'title': 'tooltip', 'aria-label': 'label read aloud by screen readers',
              'placeholder': 'hint shown inside the search box', 'description': 'page description shown in search results and link previews',
              'aria-roledescription': 'word screen readers use for each slide', 'alt': 'image description'}


def element_kind(found):
    if found.attr:
        return ATTR_WORDS.get(found.attr, found.attr)
    name, tag = found.stack[-1] if found.stack else ('', None)
    classes = tag.classes() if tag is not None else []
    if 'eyebrow' in classes:
        return 'small label above the heading'
    if 'pill' in classes:
        return 'result tag'
    if 'brand-name' in classes:
        return 'site name in the top bar'
    if name == 'span' and 'title' in classes:
        return 'title in the presentation bar'
    if name == 'a':
        return 'button' if {'btn', 'nav-cta', 'primary'} & set(classes) else 'link'
    if any(n == 'summary' for n, _ in found.stack):
        return 'title of a section that opens and closes'
    if 'deck-hint' in classes:
        return 'keyboard hint in the presentation bar'
    if 'status' in classes:
        return 'message while the document loads'
    for n, w in ELEMENT_WORDS:
        if n == name:
            return w
    if any(n == 'footer' for n, _ in found.stack):
        return 'note in the footer'
    return 'text'


def stack_has(found, pred):
    return any(pred(name, tag) for name, tag in found.stack)


def _sr_only(found):
    return stack_has(found, lambda n, t: 'sr-only' in t.classes())


# ------------------------------------------------------------------ index.html

REPORT_SECTIONS = [
    ('head', 'Browser tab and link preview'), ('nav', 'Top navigation'), ('overview', 'Overview'),
    ('scenario', '1 · The scenario'), ('architecture', '2 · Before and after'), ('evidence', '3 · Evidence'),
    ('recommendation', '4 · Assessment and recommendation'), ('present', '5 · Presentation'),
    ('slides', 'The slides'), ('deck', 'Presentation viewer'), ('footer', 'Footer'),
    ('404', 'The “page not found” page (404.html)'),
]

STAGE_OF = {'BASE_NODES': 'at M0', 'N_M1': 'from M1', 'N_M2': 'from M2', 'N_M3': 'from M3', 'N_M5': 'at M5'}
EDGE_STAGE = {'E_BEFORE': 'at M0', 'E_M1': 'at M1', 'E_M2': 'at M2', 'E_M3': 'at M3 and M5'}
MAGIC = {'STAGES': [('(see Limits)', 'Limits', '#limits'), ('of Evidence.', 'Evidence', '#evidence')],
         'FAQ': [('(see Evidence)', 'Evidence', '#evidence')]}


def magic_segs(text, magic):
    """Plain text in which the page turns a few exact phrases into links: mark those words as links."""
    segs, links, pos = [], [], 0
    hits = []
    for phrase, word, href in magic:
        at = text.find(phrase)
        if at >= 0:
            w = at + phrase.index(word)
            hits.append((w, w + len(word), href, phrase))
    for a, b, href, phrase in sorted(hits):
        if a > pos:
            segs.append(Seg(text[pos:a]))
        links.append({'href': href, 'magic': phrase})
        segs.append(Seg(text[a:b], link=len(links) - 1))
        pos = b
    if pos < len(text):
        segs.append(Seg(text[pos:]))
    return normalize(segs), links

# Interface messages in the page script, by (function, where the text goes): see ui_message().
G_DIAGRAM = 'Diagram: legend, buttons, and details panel'
REPORT_UI = {
    ('createArchitecture', 'obj:kindLabel.blocked'): ('architecture', G_DIAGRAM, 'Details panel · tag for a line that is refused or removed'),
    ('createArchitecture', 'obj:kindLabel.risk'): ('architecture', G_DIAGRAM, 'Details panel · tag for a risky line'),
    ('createArchitecture', 'obj:kindLabel.warn'): ('architecture', G_DIAGRAM, 'Details panel · tag for a line that works but too broadly or unscreened'),
    ('createArchitecture', 'obj:kindLabel.ok'): ('architecture', G_DIAGRAM, 'Details panel · tag for an allowed line'),
    ('createArchitecture', "set:$('.stage-count',host).textContent"): ('architecture', G_DIAGRAM, 'Counter between the Previous and Next buttons ({1} stage number, {2} number of stages, {3} mission)'),
    ('createArchitecture', 'set:if(announce)announce.textContent'): ('architecture', G_DIAGRAM, 'Read aloud by screen readers when a box is selected ({1} is the box name)'),
    (None, 'set:b.textContent?0'): ('evidence', '', 'Evidence · button on an open row'),
    (None, 'set:b.textContent?1'): ('evidence', '', 'Evidence · button on each row'),
    (None, "set:$('#fact-changes').textContent"): ('evidence', '', 'Change log · summary line ({1} number of changes, {2} number that can be reversed)'),
    (None, "set:$('.ledger-tools .chip[data-phase=\"ALL\"]').textContent"): ('evidence', '', 'Change log · filter button ({1} is a count)'),
    (None, "set:$('.ledger-tools .chip[data-phase=\"M1\"]').textContent"): ('evidence', '', 'Change log · filter button ({1} is a count)'),
    (None, "set:$('.ledger-tools .chip[data-phase=\"M2\"]').textContent"): ('evidence', '', 'Change log · filter button ({1} is a count)'),
    ('filterLedger', 'set:b.textContent'): ('evidence', '', 'Change log · button that shows a rollback command'),
    ('filterLedger', "set:$('#ledger-count').textContent"): ('evidence', '', 'Change log · count under the filters ({1} shown, {2} total)'),
    ('toggleCommand', 'set:b.textContent?0'): ('evidence', '', 'Change log · button that hides a rollback command'),
    ('toggleCommand', 'set:b.textContent?1'): ('evidence', '', 'Change log · button that shows a rollback command'),
    (None, 'call:toast#0'): ('evidence', '', 'Change log · message after the Copy button'),
    ('renderSlide', 'var:label'): ('deck', '', 'Read aloud by screen readers on each slide ({1} slide number, {2} number of slides, {3} slide title)'),
    (None, 'set:fsBtn.textContent?0'): ('deck', '', 'Button in full-screen mode'),
    (None, 'set:fsBtn.textContent?1'): ('deck', '', 'Button'),
}

# Static fallbacks the script overwrites as soon as the page loads: not exported, kept in step on import.
REPORT_DERIVED = [
    # (how to find the element in the HTML, where the script writes its text)
    ('<span id="fact-changes">', "set:$('#fact-changes').textContent"),
    ('data-phase="ALL" aria-pressed="true">', "set:$('.ledger-tools .chip[data-phase=\"ALL\"]').textContent"),
    ('data-phase="M1" aria-pressed="false">', "set:$('.ledger-tools .chip[data-phase=\"M1\"]').textContent"),
    ('data-phase="M2" aria-pressed="false">', "set:$('.ledger-tools .chip[data-phase=\"M2\"]').textContent"),
]

# Where labels for text in the page script's HTML templates, by the text the writer sees.
REPORT_TEMPLATE_WHERE = {
    'At the start: {1}': ('scenario', '', 'Cards · label before each card’s starting problem ({1} is that card’s text)'),
    'Selected component': ('architecture', 'Diagram: legend, buttons, and details panel', 'Details panel · heading'),
    'Scroll sideways to see the whole diagram →': ('architecture', 'Diagram: legend, buttons, and details panel', 'Hint under the diagram on narrow screens'),
    'Positions are schematic.': ('architecture', 'Diagram: legend, buttons, and details panel', 'Note under the diagram'),
    'Boxes:': ('architecture', 'Diagram: legend, buttons, and details panel', 'Legend · label before the box colors'),
    'Connections at this stage': ('architecture', 'Diagram: legend, buttons, and details panel', 'Details panel · heading'),
    'Technical details': ('architecture', 'Diagram: legend, buttons, and details panel', 'Details panel · title of the section that opens and closes'),
    'No connections at this stage.': ('architecture', 'Diagram: legend, buttons, and details panel', 'Details panel · for a box with no lines (not shown with the current diagram)'),
    'fine or fixed': ('architecture', 'Diagram: legend, buttons, and details panel', 'Legend · meaning of a green box'),
    'partly fixed or open': ('architecture', 'Diagram: legend, buttons, and details panel', 'Legend · meaning of an amber box'),
    'problem': ('architecture', 'Diagram: legend, buttons, and details panel', 'Legend · meaning of a red box'),
    'unwanted caller, refused': ('architecture', 'Diagram: legend, buttons, and details panel', 'Legend · meaning of a gray box'),
    'Change log': ('evidence', '', 'Change log · heading read aloud by screen readers'),
    'At this stage: {1}': ('architecture', 'Diagram: legend, buttons, and details panel', 'Details panel · label before the box’s status ({1} is the status line)'),
    'At this stage: Not present yet': ('architecture', 'Diagram: legend, buttons, and details panel', 'Details panel · for a box that does not exist yet at a stage (not shown with the current diagram)'),
    'Connections at this stage:': ('architecture', 'Diagram: legend, buttons, and details panel', 'Read aloud by screen readers before the list of lines'),
    '{1} to {2}: {3} ({4})': ('architecture', 'Diagram: legend, buttons, and details panel', 'Read aloud by screen readers for each line'),
    'Copy': ('evidence', '', 'Change log · button that copies a rollback command'),
    'Show': ('evidence', '', 'Change log · button that shows a rollback command'),
    'Not reversible by command': ('evidence', '', 'Change log · shown instead of the buttons when a change cannot be undone by a command'),
    'No change matches this filter.': ('evidence', '', 'Change log · shown when the search finds nothing'),
    'Swipe the diagram sideways to see the rest.': ('slides', '', 'Hint under the diagram slides on phones'),
}

REPORT_ANCHORS = {   # where JavaScript-built text sits in reading order: after this marker in index.html
    'scenario:glossary': 'id="glossary"', 'scenario:cards': 'id="cast"', 'architecture': 'id="arch-main"',
    'evidence:table': '<tbody></tbody>', 'evidence:ledger': 'id="ledger-table"', 'recommendation:controls': 'id="controls-table"',
    'recommendation:faq': 'id="faq"', 'slides': '<!-- ================= DECK OVERLAY', 'deck': 'class="deck-bar"',
}


def report_pieces(src, path='index.html'):
    pieces = []
    m = re.search(r'<script>(.*?)</script>', src, re.S)
    script_start, script_end = m.start(1), m.end(1)

    def find(marker, start=0):
        at = src.find(marker, start)
        if at < 0:
            raise ValueError('index.html no longer contains %r, which the text review tool uses to place text in '
                             'reading order. Update REPORT_ANCHORS in scripts/textreview/pages.py.' % marker)
        return at

    def anchor(name, nth=0):
        marker = REPORT_ANCHORS[name]
        if name == 'evidence:table':
            return find(marker, find('id="evidence-table"'))
        if name in ('evidence:ledger', 'recommendation:controls'):
            return find('</thead>', find(marker))   # rows come after the column headings
        return find(marker)

    # ---- static HTML
    sections = [(mm.start(), mm.group(1)) for mm in re.finditer(r'<section[^>]*\bid="([\w-]+)"', src)]
    footer_at, deck_at, nav_at, main_at = find('<footer'), find('<div class="deck"'), find('<nav'), find('<main')
    derived_starts = {src.index(mk) + len(mk) for mk, _ in REPORT_DERIVED if mk in src}
    body_at = find('<body')
    counters = {}
    headings = {}

    def static_section(pos):
        if pos < body_at:
            return 'head'
        if pos < main_at:
            return 'nav'
        if pos >= deck_at:
            return 'deck'
        if pos >= footer_at:
            return 'footer'
        sec = 'overview'
        for at, sid in sections:
            if at <= pos:
                sec = sid
        return sec

    for f in find_units(Fragment(src), skip_inside=lambda t: t.name == 'button' and 'chip' in t.classes()):
        if f.start in derived_starts:
            continue
        sec = static_section(f.start)
        kind = element_kind(f)
        if kind in ('paragraph', 'list item'):
            parent = next((t.start for n, t in reversed(f.stack) if n in ('ul', 'ol')), None) if kind == 'list item' else None
            ck = (sec, kind, parent, headings.get(sec))
            counters[ck] = counters.get(ck, 0) + 1
            kind = '%s %d' % (kind, counters[ck])
            if headings.get(sec):
                kind = 'under “%s” · %s' % (headings[sec], kind)
        if kind in ('heading', 'title') and not f.attr:
            headings[sec] = ''.join(x.text for x in f.segs)
        if _sr_only(f) and not f.attr:
            kind += ' (read aloud by screen readers only)'
        group = ''
        if stack_has(f, lambda n, t: t.attr('id') == 'print-after'):
            group, kind = 'Printed copy only', kind + ' (appears only when the page is printed)'
        occ = Occurrence(path, f.start, f.end, 'attr' if f.attr else 'html', quote=f.quote, links=f.links,
                         locks=f.locks, tags=dict(f.tags, attr_quote=f.quote) if f.attr else f.tags,
                         rich=not f.attr and not stack_has(f, lambda n, t: n == 'title'),
                         where=kind[0].upper() + kind[1:],
                         section=sec, group=group, order=(f.start, 0, 0, 0))
        pieces.append(Piece(f.segs, occ))

    # ---- script
    toks, _ = lex(src, script_start, script_end)
    annotate(toks)
    alltoks = list(walk(toks))

    # look-ups for descriptions
    def collect(const, pred):
        out = {}
        for t in alltoks:
            if t.path and t.path[0][1] == const and t.kind in ('str', 'num'):
                k = pred(_frames(t), t)
                if k is not None:
                    out[k] = t.value
        return out

    card_names = collect('CAST', lambda fr, t: _f(fr, 0)[1] if _f(fr, 1)[1] == 'name' and len(fr) == 2 else None)
    node_names = collect('NODES', lambda fr, t: _f(fr, 0)[1] if _f(fr, 1)[1] == 'name' and len(fr) == 2 else None)
    stage_codes = collect('STAGES', lambda fr, t: _f(fr, 0)[1] if _f(fr, 1)[1] == 'code' else None)
    checks = collect('EVIDENCE', lambda fr, t: _f(fr, 0)[1] if _f(fr, 1)[1] == 'check' else None)
    ledger_no = collect('LEDGER', lambda fr, t: _f(fr, 0)[1] if _f(fr, 1)[1] == 0 and t.kind == 'num' else None)
    edge_ends = {}
    for t in alltoks:
        if t.path and str(t.path[0][1]).startswith('E_') and t.kind == 'str' and len(_frames(t)) == 2 and _f(_frames(t), 1)[1] in (0, 1):
            edge_ends.setdefault(_f(_frames(t), 1)[3], {})[_f(_frames(t), 1)[1]] = t.value

    banners = [(mm.start(), mm.group(1).strip()) for mm in re.finditer(r'/\*\s*=+\s*\n\s*([^\n]+?)\s*\n\s*=+\s*\*/', src)]

    def region(pos):
        name = ''
        for at, b in banners:
            if at <= pos:
                name = b
        return name

    def add_js(tok, segs, ctx, where, section, group, order, locks=None, magic=(), locked=False, note='', span=None, rich=False):
        a, b = span if span else (tok.start + 1, tok.end - 1)
        links = []
        if magic:
            segs, links = magic_segs(tok.value, magic)
            kept = [phrase for phrase, word, href in magic if phrase in tok.value]
            if kept:
                note = ' '.join('Keep the words \u201c%s\u201d exactly as they are: the page turns them into the link.' % k for k in kept)
        occ = Occurrence(path, a, b, ctx, quote=src[tok.start] if tok.kind in ('str', 'tpl') else '', locks=locks or [],
                         links=links, magic=list(magic), rich=rich, where=where, section=section, group=group,
                         order=order, locked=locked, note=note)
        pieces.append(Piece(segs, occ))

    def add_fragment(tok, frag, section, group, order, where_prefix, overrides=None):
        for f in find_units(frag):
            shown = ''.join(s.text for s in f.segs)
            if f.attr == 'aria-label' and shown == 'Slide {1}' and 'data-i' in src[tok.start:tok.end]:
                continue   # the slide dots sit in an area hidden from screen readers, so this label is never heard
            where = element_kind(f)
            sec, grp = section, group
            if overrides and shown in overrides:
                sec, grp, where = overrides[shown]
            elif where_prefix:
                where = where_prefix + ' · ' + where
            occ = Occurrence(path, f.start, f.end, 'jsattr' if f.attr else 'jshtml', quote=src[tok.start],
                             links=f.links, locks=f.locks, tags=dict(f.tags, attr_quote=f.quote) if f.attr else f.tags,
                             rich=not f.attr,
                             where=where[0].upper() + where[1:], section=sec, group=grp, order=order[:2] + (tok.start, f.start))
            pieces.append(Piece(f.segs, occ))

    for t in alltoks:
        if t.kind not in ('str', 'tpl') or not t.path:
            continue
        const = t.path[0][1]
        fr = _frames(t)
        order_base = None
        is_str = t.kind == 'str'
        val = t.value if is_str else None

        if const == 'CAST' and is_str and not t.is_key:
            i, key = _f(fr, 0)[1], _f(fr, 1)[1]
            name = card_names.get(i, '')
            what = {'role': 'label above the name', 'name': 'name', 'what': 'description'}.get(key)
            if key == 'issue' and _f(fr, 2)[1] == 'text':
                what = 'starting problem (after “At the start:”)'
            if what:
                add_js(t, _str_value_segs(t), 'js', 'Card “%s” · %s' % (name, what), 'scenario', '', (anchor('scenario:cards'), 0, t.start, 0))
            continue
        if const == 'GLOSSARY' and is_str:
            i, j = _f(fr, 0)[1], _f(fr, 1)[1]
            add_js(t, _str_value_segs(t), 'js', 'Terms used on this page · %s %d' % ('term' if j == 0 else 'definition', i + 1),
                   'scenario', '', (anchor('scenario:glossary'), 0, t.start, 0))
            continue
        if const == 'TIERS' and is_str and _f(fr, 1)[1] == 'label':
            add_js(t, _str_value_segs(t), 'js', 'Diagram · column heading', 'architecture', 'Diagram: boxes and columns', (anchor('architecture'), 0.1, t.start, 0))
            continue
        if const == 'NODES' and is_str:
            node, key = _f(fr, 0)[1], _f(fr, 1)[1]
            name = node_names.get(node, node)
            if key in ('name', 'kind') and len(fr) == 2:
                add_js(t, _str_value_segs(t), 'js', 'Diagram · %s' % ('name of a box' if key == 'name' else 'subtitle of the box “%s”' % name),
                       'architecture', 'Diagram: boxes and columns', (anchor('architecture'), 0.1, t.start, 0))
            elif key == 'tech' and len(fr) == 3:
                if t.is_key:
                    add_js(t, _str_value_segs(t), 'js', 'Technical details of “%s” · label' % name, 'architecture',
                           'Details panel: technical details', (anchor('architecture'), 0.5, t.start, 0))
                elif t.concat:
                    chain = concat_chain(alltoks, t)
                    if chain is not None and chain[0] is t:
                        segs, locks, a, b, parts = chain_piece(src, chain)
                        if has_words(segs) and ' ' in ''.join(x.text for x in segs if x.lock < 0).strip():
                            add_js(t, segs, 'jscat', 'Technical details of “%s” · %s' % (name, _f(fr, 2)[1]), 'architecture',
                                   'Details panel: technical details', (anchor('architecture'), 0.5, t.start, 0), locks=locks,
                                   span=(a, b), note='Technical value: keep identifiers, roles, and resource names exactly as they are.')
                            pieces[-1].occ.tags['parts'] = parts
                elif ' ' in t.value.strip():
                    add_js(t, _str_value_segs(t), 'js', 'Technical details of “%s” · %s' % (name, _f(fr, 2)[1]), 'architecture',
                           'Details panel: technical details', (anchor('architecture'), 0.5, t.start, 0),
                           note='Technical value: keep identifiers, roles, and resource names exactly as they are.')
            continue
        if const in STAGE_OF and is_str:
            call = [x for x in fr if x[0] == '(' and x[2] == 'S']
            if call and call[-1][1] == 1:
                node = None
                for x in fr:
                    if x[0] == '{' and x[1] in node_names:
                        node = x[1]
                add_js(t, _str_value_segs(t), 'js', 'Diagram · status line under “%s” · %s' % (node_names.get(node, node), STAGE_OF[const]),
                       'architecture', 'Diagram: status line under each box', (anchor('architecture'), 0.2, t.start, 0))
            continue
        if const in EDGE_STAGE and is_str:
            if len(fr) == 2 and _f(fr, 1)[1] == 3:
                ends = edge_ends.get(_f(fr, 1)[3], {})
                add_js(t, _str_value_segs(t), 'js', 'Diagram · label on the line %s → %s · %s' % (
                    node_names.get(ends.get(0), ends.get(0)), node_names.get(ends.get(1), ends.get(1)), EDGE_STAGE[const]),
                    'architecture', 'Diagram: labels on the lines', (anchor('architecture'), 0.3, t.start, 0))
            continue
        if const == 'STATUS_WORDS' and is_str and not t.is_key:
            add_js(t, _str_value_segs(t), 'js', 'Diagram · legend words for a kind of line (also read aloud by screen readers)',
                   'architecture', 'Diagram: legend, buttons, and details panel', (anchor('architecture'), 0.4, t.start, 0))
            continue
        if const == 'STAGES' and is_str:
            i, key = _f(fr, 0)[1], _f(fr, 1)[1]
            code = stage_codes.get(i, '')
            if key == 'title':
                add_js(t, _str_value_segs(t), 'js', 'Tab title for %s (also read aloud by screen readers and printed)' % code, 'architecture', 'Tabs and captions', (anchor('architecture'), 0.05, t.start, 0))
            elif key == 'desc':
                add_js(t, _str_value_segs(t), 'js', 'Caption for %s, shown above the diagram' % code, 'architecture', 'Tabs and captions',
                       (anchor('architecture'), 0.05, t.start, 0), magic=MAGIC['STAGES'])
            continue
        if const == 'EVIDENCE':
            i, key = _f(fr, 0)[1], _f(fr, 1)[1]
            label = 'Evidence · row %d (“%s”)' % (i + 1, checks.get(i, '')) if i is not None else 'Evidence'
            if is_str and key in ('check', 'did', 'note') and len(fr) == 2 and t.value:
                what = {'check': 'name of the check', 'did': 'what was done', 'note': 'note under the result'}[key]
                add_js(t, _str_value_segs(t), 'js', '%s · %s' % (label, what), 'evidence', '', (anchor('evidence:table'), 0, t.start, 0))
            elif is_str and key == 'result' and _f(fr, 2)[1] == 'text':
                add_js(t, _str_value_segs(t), 'js', '%s · result' % label, 'evidence', '', (anchor('evidence:table'), 0, t.start, 0),
                       note='The result as recorded: keep status codes exactly as they are.')
            continue
        if const == 'LEDGER':
            if is_str and len(fr) == 2 and _f(fr, 1)[1] == 2:
                add_js(t, _str_value_segs(t), 'js', 'Change log · change %s' % ledger_no.get(_f(fr, 0)[1], ''), 'evidence', '',
                       (anchor('evidence:ledger'), 0.5, t.start, 0),
                       note='Keep identifiers, roles, and resource names exactly as they are.')
            continue
        if const == 'GATES' and is_str:
            i, key = _f(fr, 0)[1], _f(fr, 1)[1]
            if key == 't':
                add_js(t, _str_value_segs(t), 'js', 'Control %d · title (also in the verdict box and on slide 9)' % (i + 1), 'recommendation', '', (anchor('recommendation:controls'), 0, t.start, 0))
            elif key == 'p':
                add_js(t, _str_value_segs(t), 'js', 'Control %d · what it means' % (i + 1), 'recommendation', '', (anchor('recommendation:controls'), 0, t.start, 0))
            elif key == 'items' and _f(fr, 2)[0] == '[':
                add_js(t, _str_value_segs(t), 'js', 'Control %d · sign it is in place, %d' % (i + 1, _f(fr, 2)[1] + 1), 'recommendation', '', (anchor('recommendation:controls'), 0, t.start, 0))
            continue
        if const == 'FAQ' and is_str:
            i, j = _f(fr, 0)[1], _f(fr, 1)[1]
            if j == 0:
                add_js(t, _str_value_segs(t), 'js', 'Question %d, as asked at the workshop' % (i + 1), 'recommendation', '',
                       (anchor('recommendation:faq'), 0, t.start, 0), locked=True,
                       note='Kept word for word, as recorded at the workshop; shown here so the answer below has its question.')
            else:
                add_js(t, _str_value_segs(t), 'js', 'Answer to question %d' % (i + 1), 'recommendation', '',
                       (anchor('recommendation:faq'), 0, t.start, 0), magic=MAGIC['FAQ'])
            continue
        if const == 'SLIDES':
            i, key = _f(fr, 0)[1], _f(fr, 1)[1]
            grp = 'Slide %d' % (i + 1)
            base = (anchor('slides'), i + 1)
            if is_str and key in ('eyebrow', 'title', 'caption'):
                what = {'eyebrow': 'small label above the title', 'title': 'title', 'caption': 'note under the diagram'}[key]
                add_js(t, _str_value_segs(t), 'js', what[0].upper() + what[1:], 'slides', grp, base + (t.start, 0))
            elif t.kind == 'tpl' and key == 'html' and len(fr) == 2:
                add_fragment(t, _tpl_fragment(src, t), 'slides', grp, base, '')
            continue
        if const in ('PROJECT_ID', 'PMA_PRINCIPAL', 'CPA_PRINCIPAL', 'capHtml', 'BASE_NODES', 'N_M1', 'N_M2', 'N_M3', 'N_M5'):
            continue

        # ---- the rest of the script: HTML templates and listed interface messages
        calls = [x for x in fr if x[0] == '(']
        if calls and calls[-1][2] == 'replace':
            continue
        sk = _skeleton(src, t)
        if not has_markup(sk):
            continue
        if True:
            if t.path and any(x[0] == '${' for x in t.path) and const in ('EVIDENCE', 'LEDGER'):
                continue
            reg = region(t.start)
            sec = {'SCENARIO · GLOSSARY': 'scenario', 'ARCHITECTURE ENGINE': 'architecture', 'EVIDENCE · LEDGER': 'evidence',
                   'RECOMMENDATION': 'recommendation', 'PRESENTATION': 'deck'}.get(reg, 'deck')
            grp = 'Diagram: legend, buttons, and details panel' if sec == 'architecture' else ''
            order = {'scenario': (anchor('scenario:cards'), 0.5), 'architecture': (anchor('architecture'), 0.4),
                     'evidence': (anchor('evidence:ledger'), 0.6), 'recommendation': (anchor('recommendation:faq'), 0.5),
                     'deck': (anchor('deck'), 0)}[sec]
            if const == 'createArchitecture' or reg == 'ARCHITECTURE ENGINE':
                sec, grp, order = 'architecture', 'Diagram: legend, buttons, and details panel', (anchor('architecture'), 0.4)
            if 'beforeprint' in src[max(0, t.start - 400):t.start] and reg == 'PRESENTATION':
                continue   # the printed copy of the stage captions repeats STAGES
            frag = _tpl_fragment(src, t) if t.kind == 'tpl' else _str_fragment(src, t)
            add_fragment(t, frag, sec, grp, order, '', overrides=REPORT_TEMPLATE_WHERE)
            continue
    # ---- interface messages: text the script puts into the page, found by where it goes
    DATA = {'CAST', 'GLOSSARY', 'TIERS', 'NODES', 'STATUS_WORDS', 'STAGES', 'EVIDENCE', 'LEDGER', 'GATES', 'FAQ', 'SLIDES',
            'PROJECT_ID', 'PMA_PRINCIPAL', 'CPA_PRINCIPAL', 'capHtml'} | set(STAGE_OF) | set(EDGE_STAGE)
    for L in token_lists(toks):
        for i, t in enumerate(L):
            if t.kind not in ('str', 'tpl') or not t.path or t.path[0][1] in DATA:
                continue
            if has_markup(_skeleton(src, t)):
                continue
            sig = ui_message(L, i, t.path[0][1])
            if sig is None:
                continue
            sec, grp, where = REPORT_UI.get((t.path[0][1], sig), ('deck', '', 'Text shown by the page’s script'))
            order = {'architecture': (anchor('architecture'), 0.45), 'evidence': (anchor('evidence:ledger'), 0.7),
                     'deck': (anchor('deck'), 0.5)}.get(sec, (anchor('deck'), 0.5)) + (t.start, 0)
            if t.kind == 'str':
                segs = _str_value_segs(t)
                if not has_words(segs):
                    continue
                add_js(t, segs, 'js', where, sec, grp, order)
            else:
                segs, locks, a, b = _template_text_piece(src, t)
                if not has_words(segs):
                    continue
                add_js(t, segs, 'jstpl', where, sec, grp, order, locks=locks, span=(a, b))
            pieces[-1].occ.tags['sink'] = sig
    return pieces


# ------------------------------------------------------------------ interface messages, found by where they go

TEXT_PROPS = {'textContent', 'innerText', 'title', 'placeholder', 'ariaLabel', 'alt'}
CALL_SINKS = {('toast', 0), ('mkBtn', 0), ('mkBtn', 1), ('showDiagramFallback', 1), ('link', 2), ('showError', 0),
              ('updateToolbar', 1)}
VAR_SINKS = {('renderSlide', 'label'), ('loadDoc', 'title')}
OBJ_SINKS = {'kindLabel'}
ATTR_NAMES = {'aria-label', 'title', 'placeholder', 'alt'}


def token_lists(toks):
    """The top-level token list and every template expression's list, so a token's neighbours can be read."""
    yield toks
    for t in toks:
        if t.kind == 'tpl':
            for part in t.parts:
                if part[0] == 'expr':
                    yield from token_lists(part[3])


def sink(L, i):
    """Where a string's value goes, as a stable signature (it does not depend on the text itself):
    'set:<target>' for an assignment to a text property, 'var:<name>' for a declared variable,
    'call:<callee>#<argument>' for a call argument, or None. A ternary adds '?0' or '?1' for its branch."""
    branch = ''
    depth = 0
    j = i - 1
    while j >= 0:
        t = L[j]
        if t.kind == 'punct':
            v = t.value
            if v in ')]}':
                depth += 1
            elif v in '([{':
                if depth == 0:
                    if v == '(' and j > 0 and L[j - 1].kind == 'ident':
                        callee = L[j - 1].value
                        arg = sum(1 for x in L[j + 1:i] if x.kind == 'punct' and x.value == ',' and _depth_between(L, j + 1, L.index(x)) == 0)
                        if callee == 'setAttribute' and arg == 1 and L[j + 1].kind == 'str' and L[j + 1].value in ATTR_NAMES:
                            return 'attr:%s%s' % (L[j + 1].value, branch)
                        return 'call:%s#%d%s' % (callee, arg, branch)
                    return None
                depth -= 1
            elif depth == 0:
                if v == ':' and not branch:
                    branch = '?1'
                elif v == '?' and not branch:
                    branch = '?0'
                elif v == '=' and not (j + 1 < len(L) and L[j + 1].kind == 'punct' and L[j + 1].value in '=>') \
                        and not (j > 0 and L[j - 1].kind == 'punct' and L[j - 1].value in '=!<>+-*/%&|^'):
                    k = j - 1
                    target = []
                    while k >= 0 and not (L[k].kind == 'punct' and L[k].value in ';{}') :
                        target.insert(0, L[k]); k -= 1
                    names = [x.value for x in target if x.kind == 'ident']
                    if target and target[0].kind == 'ident' and target[0].value in ('const', 'let', 'var') and len(names) >= 2:
                        return 'var:%s%s' % (names[1], branch)
                    if len(target) >= 2 and target[-2].kind == 'punct' and target[-2].value == '.' and target[-1].value in TEXT_PROPS:
                        return 'set:%s%s' % (''.join(x.value if x.kind != 'str' else repr(x.value) for x in target), branch)
                    return None
                elif v == ';':
                    return None
        j -= 1
    return None


def _depth_between(L, a, b):
    d = 0
    for x in L[a:b]:
        if x.kind == 'punct':
            if x.value in '([{':
                d += 1
            elif x.value in ')]}':
                d -= 1
    return d


def ui_message(L, i, func):
    """Whether this string or template is an interface message, and its signature."""
    t = L[i]
    fr = t.path[1:] if t.path else ()
    for f in fr:
        if f[0] == '{' and f[2] in OBJ_SINKS and not t.is_key:
            return 'obj:%s.%s' % (f[2], f[1])
    s = sink(L, i)
    if s is None:
        return None
    base = s.split('?')[0]
    if base.startswith(('set:', 'attr:')):
        return s
    if base.startswith('call:'):
        callee, arg = base[5:].split('#')
        return s if (callee, int(arg)) in CALL_SINKS else None
    if base.startswith('var:'):
        return s if (func, base[4:]) in VAR_SINKS else None
    return None



# ------------------------------------------------------------------ 'text' + NAME + 'text'

def concat_chain(alltoks, t):
    """The tokens of a string concatenation ('a' + NAME + 'b') that t belongs to, if it is only strings and
    plain names joined with +; otherwise None."""
    idx = alltoks.index(t)
    i = idx
    while i >= 2 and alltoks[i - 1].kind == 'punct' and alltoks[i - 1].value == '+' and alltoks[i - 2].kind in ('str', 'ident'):
        i -= 2
    j = idx
    while j + 2 < len(alltoks) and alltoks[j + 1].kind == 'punct' and alltoks[j + 1].value == '+' and alltoks[j + 2].kind in ('str', 'ident'):
        j += 2
    chain = alltoks[i:j + 1:2]
    if any(x.kind == 'ident' and not x.value.isupper() and not x.value.replace('_', '').isupper() for x in chain):
        return None
    return chain


def chain_piece(src, chain):
    """Text of a concatenation: string parts are editable, names (such as PROJECT_ID) are locked."""
    segs, locks, parts = [], [], []
    for x in chain:
        if x.kind == 'str':
            segs.append(Seg(x.value))
            parts.append(('str', x.quote))
        else:
            locks.append({'src': x.value, 'text': '', 'kind': 'expr', 'name': x.value})
            segs.append(Seg('', lock=len(locks) - 1))
            parts.append(('name', x.value))
    segs = normalize(segs)
    number_locks(segs, locks)
    return segs, locks, chain[0].start, chain[-1].end, parts


def simple_page_pieces(src, path):
    """A page without a script of its own (404.html): its HTML text only."""
    pieces = []
    for f in find_units(Fragment(src)):
        kind = element_kind(f)
        occ = Occurrence(path, f.start, f.end, 'attr' if f.attr else 'html', quote=f.quote, links=f.links, locks=f.locks,
                         tags=dict(f.tags, attr_quote=f.quote) if f.attr else f.tags,
                         rich=not f.attr and not stack_has(f, lambda n, t: n == 'title'),
                         where=kind[0].upper() + kind[1:], section='404', group='', order=(f.start, 0, 0, 0))
        pieces.append(Piece(f.segs, occ))
    return pieces
