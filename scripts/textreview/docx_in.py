"""Read a reviewed document back: for every row, its ID and its text as it reads with all tracked changes
accepted, with bold, italic, links, and the writer's comments. Handles what Word, Google Docs, Pages, and
LibreOffice write: tracked insertions, deletions and moves, deleted paragraph marks, hyperlink fields, links
split into several pieces, content controls, character styles, symbol fonts, and added, deleted, or reshaped
rows."""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
STRICT_NS = 'http://purl.oclc.org/ooxml/wordprocessingml/main'
W = '{%s}' % W_NS
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
ID_RE = re.compile(r'ID\s*([0-9a-f]{8})(?![0-9a-f])(?:\s*\u00b7\s*([0-9a-f]{8})(?![0-9a-f]))?')
ID_LINE_RE = re.compile(r'\s*ID\s*[0-9a-f]{8}(?:\s*\u00b7\s*[0-9a-f]{8})?\s*')
HEADER_LEFT = 'Where it appears'
MONO_WORDS = ('courier', 'consolas', 'menlo', 'monaco', 'mono', 'lucida console', 'andale', 'inconsolata', 'fira code',
              'source code', 'roboto mono', 'jetbrains')
INLINE_CONTAINERS = {W + 'smartTag', W + 'customXml', W + 'ins', W + 'moveTo', W + 'dir', W + 'bdo'}
SKIPPED_CONTAINERS = {W + 'del', W + 'moveFrom'}

# Characters of the Symbol font (Word's Insert > Symbol and some AutoCorrect entries store them by code).
SYMBOL_FONT = {
    0x22: '∀', 0x24: '∃', 0x27: '∋', 0x2A: '∗', 0x2D: '−', 0x40: '≅', 0x5C: '∴', 0x5E: '⊥', 0x7E: '∼',
    0xA2: '′', 0xA3: '≤', 0xA4: '⁄', 0xA5: '∞', 0xA6: 'ƒ', 0xA7: '♣', 0xA8: '♦', 0xA9: '♥', 0xAA: '♠', 0xAB: '↔',
    0xAC: '←', 0xAD: '↑', 0xAE: '→', 0xAF: '↓', 0xB0: '°', 0xB1: '±', 0xB2: '″', 0xB3: '≥', 0xB4: '×', 0xB5: '∝',
    0xB6: '∂', 0xB7: '•', 0xB8: '÷', 0xB9: '≠', 0xBA: '≡', 0xBB: '≈', 0xBC: '…', 0xBF: '↵', 0xC4: '⊗', 0xC5: '⊕',
    0xC6: '∅', 0xC7: '∩', 0xC8: '∪', 0xC9: '⊃', 0xCA: '⊇', 0xCB: '⊄', 0xCC: '⊂', 0xCD: '⊆', 0xCE: '∈', 0xCF: '∉',
    0xD0: '∠', 0xD1: '∇', 0xD2: '®', 0xD3: '©', 0xD4: '™', 0xD5: '∏', 0xD6: '√', 0xD7: '⋅', 0xD8: '¬', 0xD9: '∧',
    0xDA: '∨', 0xDB: '⇔', 0xDC: '⇐', 0xDD: '⇑', 0xDE: '⇒', 0xDF: '⇓', 0xE0: '◊', 0xE5: '∑', 0xF2: '∫',
}
SYMBOL_FONT.update({c: chr(c) for c in range(0x20, 0x7F) if c not in SYMBOL_FONT and not (0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A)})
SYMBOL_FONT.update(dict(zip(range(0x41, 0x5B), 'ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ')))
SYMBOL_FONT.update(dict(zip(range(0x61, 0x7B), 'αβχδεφγηιϕκλμνοπθρστυϖωξψζ')))
# The Wingdings characters Word's AutoCorrect inserts (--> <-- :) :| :( ) and the common check marks.
WINGDINGS = {0x4A: '☺', 0x4B: '😐', 0x4C: '☹', 0xDF: '←', 0xE0: '→', 0xE1: '↑', 0xE2: '↓', 0xFC: '✓', 0xFB: '✗'}


class NotAReviewDocument(Exception):
    pass


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    mono: bool = False
    link: Optional[str] = None     # target of the hyperlink this text sits in
    link_no: int = -1              # which link in the cell (one number per link, even if stored in pieces)
    struck: bool = False
    caps: bool = False
    bad_symbol: str = ''           # the font of a symbol that cannot be translated


@dataclass
class Row:
    key: Optional[str]
    left: str
    paragraphs: List[List[Span]]
    comments: List[dict] = field(default_factory=list)
    deleted: bool = False
    inserted: bool = False
    shape: str = ''                # why the row's shape cannot be trusted ('' when it is fine)
    extra_text: str = ''           # text in cells the export did not create
    header: bool = False
    text_hash: Optional[str] = None   # fingerprint of the exported words, to tell untouched rows apart


@dataclass
class Review:
    rows: List[Row]
    comments: dict          # id -> {'author', 'date', 'text'}
    loose_comments: list    # comments not anchored in any row
    ids_outside_tables: int = 0


def _on(el):
    if el is None:
        return None
    v = el.get(W + 'val')
    return v is None or v.lower() not in ('0', 'false', 'off', 'none')


class Styles:
    def __init__(self, xml):
        self.char = {}
        if not xml:
            return
        root = ET.fromstring(xml)
        for st in root.iter(W + 'style'):
            if st.get(W + 'type') != 'character':
                continue
            sid = st.get(W + 'styleId')
            rpr = st.find(W + 'rPr')
            based = st.find(W + 'basedOn')
            name = st.find(W + 'name')
            self.char[sid] = (rpr, based.get(W + 'val') if based is not None else None,
                              name.get(W + 'val') if name is not None else (sid or ''))

    def resolve(self, sid, prop, depth=0):
        if sid not in self.char or depth > 10:
            return None
        rpr, based, _ = self.char[sid]
        if rpr is not None:
            el = rpr.find(W + prop)
            if el is not None:
                return el
        return self.resolve(based, prop, depth + 1) if based else None

    def name(self, sid):
        return (self.char.get(sid) or (None, None, sid or ''))[2] or ''


def _run_props(r, styles):
    rpr = r.find(W + 'rPr')
    sid = None
    if rpr is not None and rpr.find(W + 'rStyle') is not None:
        sid = rpr.find(W + 'rStyle').get(W + 'val')

    def prop(name):
        el = rpr.find(W + name) if rpr is not None else None
        if el is None and sid:
            el = styles.resolve(sid, name)
        return el

    bold = bool(_on(prop('b')))
    italic = bool(_on(prop('i')))
    struck = bool(_on(prop('strike'))) or bool(_on(prop('dstrike')))
    hidden = bool(_on(prop('vanish')))
    caps = bool(_on(prop('caps'))) or bool(_on(prop('smallCaps')))
    fonts = prop('rFonts')
    mono = False
    if fonts is not None:
        names = ' '.join((fonts.get(W + a) or '') for a in ('ascii', 'hAnsi')).lower()
        mono = any(m in names for m in MONO_WORDS)
    sname = styles.name(sid).lower() if sid else ''
    if 'strong' in sname and prop('b') is None:
        bold = True
    if sname == 'emphasis' and prop('i') is None:
        italic = True
    if 'code' in sname or 'source text' in sname:
        mono = True
    return bold, italic, mono, struck, hidden, caps


def _symbol(font, code):
    """The character a w:sym element shows, or ('', reason) when it cannot be translated safely."""
    try:
        n = int(code, 16)
    except ValueError:
        return '', font or 'unknown'
    if n >= 0xF000:
        n -= 0xF000
    f = (font or '').lower()
    if f == 'symbol' and n in SYMBOL_FONT:
        return SYMBOL_FONT[n], ''
    if f.startswith('wingdings') and n in WINGDINGS:
        return WINGDINGS[n], ''
    if not f or f in ('calibri', 'arial', 'times new roman', 'aptos', 'helvetica'):
        return chr(n), ''
    return '', font


class _Cell:
    """Accumulates the visible text of one table cell."""

    def __init__(self, rels, styles):
        self.rels, self.styles = rels, styles
        self.paragraphs = [[]]
        self.fields = []          # complex fields: {'instr': str, 'phase': 'instr'|'result'}
        self.link_counter = 0
        self.last_link = None     # (target, number) of the most recent link
        self.unlinked_since = True
        self.comment_starts, self.comment_refs = set(), set()
        self.join_next = False

    def new_paragraph(self):
        if self.join_next:          # the previous paragraph's mark was deleted: the two paragraphs are one
            self.join_next = False
            return
        if self.paragraphs[-1]:
            self.paragraphs.append([])

    def link_number(self, target):
        """One number per link: pieces of the same link next to each other share it."""
        if self.last_link and self.last_link[0] == target and not self.unlinked_since:
            return self.last_link[1]
        self.link_counter += 1
        self.last_link = (target, self.link_counter)
        return self.link_counter

    def field_link(self):
        for f in reversed(self.fields):
            if f['phase'] == 'result':
                m = re.match(r'\s*HYPERLINK\s+(?:"([^"]*)"|(\S+))?(?:.*?\\l\s+"([^"]*)")?', f['instr'])
                if m:
                    target = m.group(1) or m.group(2) or ''
                    if m.group(3):
                        target += '#' + m.group(3)
                    if 'no' not in f:
                        f['no'] = self.link_number(target)
                    return target, f['no']
        return None, -1

    def inline(self, el, link=None, link_no=-1):
        for ch in el:
            tag = ch.tag
            if tag == W + 'r':
                self.run(ch, link, link_no)
            elif tag == W + 'hyperlink':
                rid = ch.get(R + 'id')
                target = self.rels.get(rid, '') if rid else ''
                if ch.get(W + 'anchor'):
                    target = (target or '') + '#' + ch.get(W + 'anchor')
                target = target or '#'
                self.inline(ch, target, self.link_number(target))
            elif tag in INLINE_CONTAINERS:
                self.inline(ch, link, link_no)
            elif tag in SKIPPED_CONTAINERS:
                continue
            elif tag == W + 'sdt':
                content = ch.find(W + 'sdtContent')
                if content is not None:
                    self.inline(content, link, link_no)
            elif tag == W + 'fldSimple':
                instr = ch.get(W + 'instr') or ''
                m = re.match(r'\s*HYPERLINK\s+"([^"]*)"', instr)
                if m:
                    self.inline(ch, m.group(1), self.link_number(m.group(1)))
                else:
                    self.inline(ch, link, link_no)
            elif tag == W + 'commentRangeStart':
                self.comment_starts.add(ch.get(W + 'id'))

    def run(self, r, link, link_no):
        bold, italic, mono, struck, hidden, caps = _run_props(r, self.styles)
        for ch in r:
            tag = ch.tag
            if tag == W + 'fldChar':
                t = ch.get(W + 'fldCharType')
                if t == 'begin':
                    self.fields.append({'instr': '', 'phase': 'instr'})
                elif t == 'separate' and self.fields:
                    self.fields[-1]['phase'] = 'result'
                elif t == 'end' and self.fields:
                    self.fields.pop()
                continue
            if tag == W + 'instrText':
                if self.fields and self.fields[-1]['phase'] == 'instr':
                    self.fields[-1]['instr'] += ch.text or ''
                continue
            if tag == W + 'commentReference':
                self.comment_refs.add(ch.get(W + 'id'))
                continue
            if self.fields and self.fields[-1]['phase'] == 'instr':
                continue
            text, bad = None, ''
            if tag == W + 't':
                text = ch.text or ''
            elif tag == W + 'tab':
                text = '\t'
            elif tag in (W + 'br', W + 'cr'):
                text = '\n' if ch.get(W + 'type') in (None, 'textWrapping') else ' '
            elif tag == W + 'noBreakHyphen':
                text = '-'
            elif tag == W + 'softHyphen':
                text = ''
            elif tag == W + 'sym':
                text, bad = _symbol(ch.get(W + 'font'), ch.get(W + 'char') or '')
            if text is None or hidden:
                continue
            flink, fno = self.field_link()
            span_link, span_no = (flink, fno) if flink is not None else (link, link_no)
            if span_link is None and text:
                self.unlinked_since = True
            elif span_link is not None:
                self.unlinked_since = False
            self.paragraphs[-1].append(Span(text, bold, italic, mono, span_link, span_no, struck, caps, bad))


def _blocks(cell_el, acc):
    for ch in cell_el:
        if ch.tag == W + 'p':
            acc.new_paragraph()
            acc.inline(ch)
            mark = ch.find('%spPr/%srPr/%sdel' % (W, W, W))
            if mark is not None:
                acc.join_next = True
        elif ch.tag == W + 'tbl':
            for tr in ch.iter(W + 'tr'):
                for tc in tr.iter(W + 'tc'):
                    _blocks(tc, acc)
        elif ch.tag == W + 'sdt':
            content = ch.find(W + 'sdtContent')
            if content is not None:
                _blocks(content, acc)
        elif ch.tag in (W + 'customXml', W + 'ins', W + 'moveTo'):
            _blocks(ch, acc)


def _row_cells(tr):
    cells = []
    for ch in tr:
        if ch.tag == W + 'tc':
            cells.append(ch)
        elif ch.tag in (W + 'sdt', W + 'customXml'):
            inner = ch.find(W + 'sdtContent') if ch.tag == W + 'sdt' else ch
            if inner is not None:
                cells.extend(c for c in inner if c.tag == W + 'tc')
    return cells


def _cell(cell, rels, styles):
    acc = _Cell(rels, styles)
    _blocks(cell, acc)
    return [p for p in acc.paragraphs if p], acc.comment_starts, acc.comment_refs


def _span(cell, name):
    tcpr = cell.find(W + 'tcPr')
    if tcpr is None:
        return None
    return tcpr.find(W + name)


def text_of(paragraphs):
    return ' '.join(''.join(s.text for s in p) for p in paragraphs).strip()


def read(path) -> Review:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        main = 'word/document.xml'
        if '_rels/.rels' in names:
            for rel in ET.fromstring(z.read('_rels/.rels')):
                if (rel.get('Type') or '').endswith('/officeDocument'):
                    main = (rel.get('Target') or main).lstrip('/')
        if main not in names:
            raise NotAReviewDocument('not a Word document')
        raw = z.read(main)
        rels_name = main.rsplit('/', 1)[0] + '/_rels/' + main.rsplit('/', 1)[1] + '.rels'
        doc = ET.fromstring(raw)
        if doc.tag.startswith('{%s}' % STRICT_NS):
            raise NotAReviewDocument('strict')
        rels = {}
        if rels_name in names:
            for rel in ET.fromstring(z.read(rels_name)):
                rels[rel.get('Id')] = rel.get('Target') or ''
        styles = Styles(z.read('word/styles.xml') if 'word/styles.xml' in names else None)
        comments = {}
        if 'word/comments.xml' in names:
            for c in ET.fromstring(z.read('word/comments.xml')).iter(W + 'comment'):
                text = '\n'.join(''.join(t.text or '' for t in p.iter(W + 't')) for p in c.iter(W + 'p')).strip()
                comments[c.get(W + 'id')] = {'author': c.get(W + 'author') or '', 'date': c.get(W + 'date') or '', 'text': text}
    rows, owner = [], {}
    in_tables = set()
    for tbl in doc.iter(W + 'tbl'):
        for p in tbl.iter(W + 'p'):
            in_tables.add(p)
        for tr in tbl:
            if tr.tag != W + 'tr':
                continue
            cells = _row_cells(tr)
            if len(cells) < 2:
                if cells:   # the two cells were merged into one: say so if the row had an ID
                    pars, s1, r1 = _cell(cells[0], rels, styles)
                    whole = '\n'.join(''.join(x.text for x in p) for p in pars)
                    m = ID_RE.search(whole)
                    if m:
                        rows.append(Row(m.group(1), whole, pars, [], False, False, 'its two cells were merged', '',
                                        text_hash=m.group(2)))
                continue
            trpr = tr.find(W + 'trPr')
            deleted = trpr is not None and trpr.find(W + 'del') is not None
            inserted = trpr is not None and trpr.find(W + 'ins') is not None
            left_pars, ls, lr = _cell(cells[0], rels, styles)
            left = '\n'.join(''.join(s.text for s in p) for p in left_pars)
            ids = [(m.group(1), m.group(2)) for m in ID_RE.finditer(left)]
            right_pars, rs, rr = _cell(cells[1], rels, styles)
            shape, extra = '', []
            if len(cells) > 2:
                for c in cells[2:]:
                    pars, s2, r2 = _cell(c, rels, styles)
                    extra.append(text_of(pars))
                    rs |= s2; rr |= r2
            span = _span(cells[1], 'gridSpan')
            if span is not None and (span.get(W + 'val') or '1') != '1':
                shape = 'cells were merged'
            if _span(cells[1], 'vMerge') is not None or _span(cells[0], 'vMerge') is not None:
                shape = 'cells were merged'
            key = ids[-1][0] if ids else None
            text_hash = (ids[-1][1] or None) if ids else None
            if key is None:
                # a deleted row (or deleted ID line): the ID is in the deleted text
                gone = ''.join(t.text or '' for t in cells[0].iter(W + 'delText'))
                m = ID_RE.search(gone)
                if m:
                    key, text_hash, deleted = m.group(1), m.group(2), True
                    if not right_pars:
                        right_pars = [[Span(''.join(t.text or '' for t in cells[1].iter(W + 'delText')))]]
            row = Row(key, left, right_pars, [], deleted, inserted, shape, ' | '.join(x for x in extra if x),
                      header=left.strip().startswith(HEADER_LEFT) and not ids, text_hash=text_hash)
            rows.append(row)
            # a comment belongs to the row where it starts (or, without a start, where it is referenced)
            for cid in (ls | rs):
                owner.setdefault(cid, row)
            for cid in (lr | rr):
                owner.setdefault(cid, row)
    for cid, row in owner.items():
        if cid in comments:
            row.comments.append(dict(comments[cid], id=cid))
    loose = [dict(c, id=i) for i, c in comments.items() if i not in owner]
    outside, first = 0, ''
    for p in doc.iter(W + 'p'):
        if p in in_tables:
            continue
        line = ''.join(t.text or '' for t in p.iter(W + 't'))
        if ID_LINE_RE.fullmatch(line):
            outside += 1
            first = first or line.strip()
    review = Review(rows, comments, loose, outside)
    review.first_outside = first
    return review
