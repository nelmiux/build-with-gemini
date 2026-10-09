"""Bring a reviewed document back: work out what changed, write it into the sources with the right escaping
for each place, keep fallback text in step, check the result, and write a report for the author.

Nothing is ever lost silently: every row that was read either becomes an edit, matches the page already, or
is listed under "Not applied" with the reason. If the document cannot be read as a review at all, nothing is
written. An edit that would not read back exactly as written is set aside on its own; the others still go in."""
from __future__ import annotations

import collections
import datetime as _dt
import difflib
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field, replace
from urllib.parse import parse_qs, urlparse

from . import docx_in
from .htmlfrag import encode, encode_plain
from .jslex import js_escape
from .model import Seg, canon, has_words, merge, normalize, plain
from .units import REPORT_FILES, all_files, build, read_sources, scope_of, text_fingerprint, unit_key

OPENERS = set(' \t\n([{<“‘—–-/ ')
NBSP = ' '


@dataclass
class Change:
    unit: object
    segs: list
    links: list                      # per link in the new text, in order: ('orig', index) or ('new', url)
    warnings: list = field(default_factory=list)
    comments: list = field(default_factory=list)
    source_doc: str = ''

    @property
    def key_after(self):
        return unit_key(by_appearance(self.segs), False, scope_of(self.unit.occs[0].file))


def by_appearance(segs):
    """Locked parts numbered in reading order, as a fresh read of the page numbers them (they keep their own
    numbers while being written, so each one's source can be found)."""
    order = {}
    for s in segs:
        if s.lock >= 0 and s.lock not in order:
            order[s.lock] = len(order)
    return [replace(s, lock=order[s.lock]) if s.lock >= 0 else s for s in segs]


@dataclass
class Skipped:
    where: str
    key: str
    reason: str
    text: str = ''
    comments: list = field(default_factory=list)
    file: str = ''
    was: str = ''


def plural(n, one, many=None):
    return '%d %s' % (n, one if n == 1 else (many or one + 's'))


# ------------------------------------------------------------------ links

def unwrap(url):
    """Google Docs may send links through https://www.google.com/url?q=..."""
    try:
        u = urlparse(url)
        if u.netloc.endswith('google.com') and u.path == '/url':
            q = parse_qs(u.query).get('q')
            if q:
                return q[0]
    except ValueError:
        pass
    return url


def same_target(a, b):
    def norm(x):
        x = unwrap(x or '').strip()
        return x[:-1] if x.endswith('/') else x
    return norm(a) == norm(b)


def export_target(href, occ, home):
    from .export import link_target
    return link_target(href, occ, home)


# ------------------------------------------------------------------ the writer's typing

def quote_style(text):
    straight = len(re.findall(r"[\"']", text))
    curly = len(re.findall('[“”‘’]', text))
    if straight and not curly:
        return 'straight'
    if curly and not straight:
        return 'curly'
    return None


def _changed_ranges(old, new):
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [(j1, j2) for op, i1, i2, j1, j2 in sm.get_opcodes() if op in ('replace', 'insert')]


def fix_typing(segs, original_text, style):
    """Word and Google Docs change quotes and spaces as you type. Make the writer's new text match the text
    around it (curly or straight quotes, plain spaces). Unchanged text is never touched."""
    text = plain(segs)
    ranges = _changed_ranges(original_text, text)
    if not ranges:
        return segs
    out, pos = [], 0
    for s in segs:
        if s.lock >= 0 or s.code:
            out.append(s); pos += len(s.text); continue
        buf = []
        for ch in s.text:
            i = pos
            pos += 1
            if not any(a <= i < b for a, b in ranges):
                buf.append(ch); continue
            prev = text[i - 1] if i else ' '
            nxt = text[i + 1] if i + 1 < len(text) else ''
            if ch == NBSP and NBSP not in original_text:
                ch = ' '
            elif style == 'straight':
                ch = {'“': '"', '”': '"', '‘': "'", '’': "'"}.get(ch, ch)
            elif ch == '"':
                ch = '“' if prev in OPENERS else '”'
            elif ch == "'":
                if prev.isalnum():
                    ch = '’'
                elif prev in OPENERS:
                    ch = '’' if nxt.isdigit() else '‘'
                else:
                    ch = '’'
            buf.append(ch)
        out.append(replace(s, text=''.join(buf)))
    return out


def relock(segs, original):
    """Put the locked parts back. Each one is found where the unchanged text around it says it is, or else
    wherever its text now is (the writer may move it). A locked part may not be removed, changed, repeated, or
    invented."""
    locks, pos = [], 0
    orig_text = plain(original)
    for s in original:
        if s.lock >= 0:
            locks.append((pos, pos + len(s.text), s))
        pos += len(s.text)
    text = plain(segs)
    if not locks:
        if re.search(r'\{\d+\}', text) and not re.search(r'\{\d+\}', orig_text):
            return None, 'The text now contains a placeholder such as {1}, which the page would show as it is. Remove it.'
        return segs, None
    flat = [(ch, s) for s in segs for ch in s.text]
    blocks = difflib.SequenceMatcher(None, orig_text, text, autojunk=False).get_matching_blocks()

    def mapped(p):
        for a, b, size in blocks:
            if a <= p < a + size:
                return b + (p - a)
        return None

    def estimate(p):
        best = 0
        for a, b, size in blocks:
            if size and a <= p:
                best = b + min(p - a, size)
        return best

    taken = []

    def free(a, b):
        return all(b <= x or a >= y for x, y, _ in taken)

    for a, b, lk in locks:
        ms, me = mapped(a), mapped(b - 1)
        if ms is not None and me is not None and me - ms == b - a - 1 and text[ms:me + 1] == lk.text and free(ms, me + 1):
            taken.append((ms, me + 1, lk))
    for a, b, lk in locks:
        if any(t[2] is lk for t in taken):
            continue
        hits = [m.start() for m in re.finditer(re.escape(lk.text), text) if free(m.start(), m.start() + len(lk.text))] if lk.text else []
        if not hits:
            return None, 'The shaded part “%s” was changed or removed; it has to stay exactly as it was.' % lk.text
        want = estimate(a)
        at = min(hits, key=lambda h: abs(h - want))
        taken.append((at, at + len(lk.text), lk))
    taken.sort(key=lambda t: t[0])
    rest = ''.join(ch for i, ch in enumerate(text) if all(not (x <= i < y) for x, y, _ in taken))
    if re.search(r'\{\d+\}', rest):
        return None, 'The text contains a placeholder such as {1} that is not one of the shaded values; it would show on the page as it is.'
    out, i, k = [], 0, 0
    while i < len(flat):
        if k < len(taken) and i == taken[k][0]:
            out.append(replace(taken[k][2]))
            i = taken[k][1]; k += 1
            continue
        ch, s = flat[i]
        out.append(Seg(ch, s.bold, s.italic, s.code, s.link, -1))
        i += 1
    return merge(out), None


def renumber_values(segs, unit_occ):
    """Values filled in by the script are shown as {1}, {2}... in reading order; after a move, number again."""
    n = 0
    out = []
    for s in segs:
        if s.lock >= 0 and unit_occ.locks[s.lock].get('kind') == 'expr':
            n += 1
            out.append(replace(s, text='{%d}' % n))
        else:
            out.append(s)
    return out


def read_edit(unit, row, styles, home):
    """The writer's version of one piece: (segments, links, warnings, None) or (None, None, warnings, reason)."""
    occ = unit.occs[0]
    warns = []
    paras = [p for p in row.paragraphs if ''.join(s.text for s in p).strip()]
    if not paras:
        return None, None, warns, 'The writer deleted this text. The import does not take text off the page; if it should go, remove it by hand.'
    if len(paras) > 1:
        warns.append('The writer’s text had %d paragraphs; they were joined into one.' % len(paras))
    spans = []
    for n, p in enumerate(paras):
        if n:
            spans.append(docx_in.Span(' '))
        spans.extend(p)
    if any(s.struck and s.text.strip() for s in spans):
        return None, None, warns, 'Part of the text is struck through (formatting, not a tracked deletion), so it is unclear what the writer meant. Make this change by hand.'
    bad = next((s.bad_symbol for s in spans if s.bad_symbol), '')
    if bad:
        return None, None, warns, 'The text contains a symbol from the %s font, which the page cannot show. Make this change by hand with the plain character (for example → or ✓).' % bad
    if any(s.caps and s.text.strip() for s in spans):
        warns.append('All Caps or Small Caps formatting was ignored; only capital letters typed as such reach the page.')
    # links: each hyperlink is matched to an original link by its address
    groups = []
    for s in spans:
        if s.link_no >= 0 and s.link_no not in [g[0] for g in groups]:
            groups.append((s.link_no, s.link or ''))
    n_orig = len(occ.links)
    targets = [export_target(l.get('href') or '', occ, home) for l in occ.links]
    mapping, new_links, used = {}, [], set()
    for no, target in groups:
        hit = next((i for i in range(n_orig) if i not in used and same_target(target, targets[i])), None)
        if hit is not None:
            mapping[no] = hit; used.add(hit)
    for no, target in groups:
        if no in mapping:
            continue
        t = unwrap(target)
        if t.startswith(('http://', 'https://', 'mailto:')):
            new_links.append(t)
            mapping[no] = n_orig + len(new_links) - 1
            warns.append('A link to %s was added.' % t)
        else:
            warns.append('A link without a usable address was left out; its words were kept.')
    keeps_code = any(s.code and s.lock < 0 for s in unit.segs)
    segs = [Seg(unicodedata.normalize('NFC', s.text), s.bold, s.italic, s.mono and keeps_code, mapping.get(s.link_no, -1)) for s in spans]
    for s in segs:
        s.text = re.sub('[\t\n\r\v\f  ]', ' ', s.text)
    segs = normalize(segs)
    style = quote_style(unit.text) or styles.get(occ.file, 'curly')
    segs = fix_typing(segs, unicodedata.normalize('NFC', unit.text), style)
    segs, err = relock(segs, [replace(s, text=unicodedata.normalize('NFC', s.text)) if s.lock < 0 else s for s in unit.segs])
    if err:
        return None, None, warns, err
    segs = normalize(renumber_values(segs, occ))
    still = {s.link for s in segs if s.link >= 0}
    for i in range(n_orig):   # a link on a shaded part comes back with it
        if i not in still and not occ.links[i].get('magic') and occ.links[i].get('href'):
            warns.append('The link to %s was removed; its words were kept.' % occ.links[i]['href'])
    rich = all(o.rich for o in unit.occs)
    if not rich:
        if any((s.bold or s.italic) and s.lock < 0 for s in segs) and not any(s.bold or s.italic for s in unit.segs):
            warns.append('Bold or italic was dropped: this text is plain on the page.')
            segs = normalize([replace(s, bold=False, italic=False) if s.lock < 0 else s for s in segs])
        if new_links:
            warns.append('New links were dropped: this text cannot hold links.')
            segs = normalize([replace(s, link=-1) if s.link >= n_orig else s for s in segs])
            new_links = []
    magic = next((o.magic for o in unit.occs if o.magic), None)
    if magic:
        from .pages import magic_segs
        new_text = plain(segs)
        for phrase, word, href in magic:
            if phrase in unit.text and phrase not in new_text:
                return None, None, warns, ('The page links “%s” only when the exact words “%s” are kept, and the writer’s version '
                                           'changes them. Keep those words, or make this change (and the link) by hand.' % (word, phrase))
        segs, magic_links = magic_segs(new_text, magic)
        kept_warns = [w for w in warns if 'link' not in w.lower()]
        if new_links:
            kept_warns.append('New links were dropped: the page adds the only link in this text by itself.')
        return segs, [('orig', i) for i in range(len(magic_links))], kept_warns, None
    if not has_words(segs):
        return None, None, warns, 'The new text has no letters left (only numbers or symbols), so the tool could not find it again next time. Make this change by hand.'
    if any(o.tags.get('sink') for o in unit.occs) and re.search(r'</?[A-Za-z!]', plain(segs)) and not re.search(r'</?[A-Za-z!]', unit.text):
        return None, None, warns, 'The new text contains something that looks like an HTML tag (<…>), which this message cannot hold. Make this change by hand.'
    # number the links in reading order, as the page will be read back
    order, links = {}, []
    for s in segs:
        if s.link >= 0 and s.link not in order:
            order[s.link] = len(links)
            links.append(('orig', s.link) if s.link < n_orig else ('new', new_links[s.link - n_orig]))
    segs = [replace(s, link=order[s.link]) if s.link >= 0 else s for s in segs]
    return segs, links, warns, None


def link_list(unit):
    """The original links of a unit in reading order, in the same form as read_edit returns."""
    order = []
    for s in unit.segs:
        if s.link >= 0 and s.link not in order:
            order.append(s.link)
    return [('orig', i) for i in order]


# ------------------------------------------------------------------ writing one occurrence

def encode_occ(segs, occ, source, links=None):
    if links is not None:
        lst = [occ.links[i] if kind == 'orig' else {'href': i} for kind, i in links]
        o = replace(occ, links=lst)
    else:
        o = occ
    if occ.ctx == 'html':
        return ''.join(p[1] for p in encode(segs, o, source))
    if occ.ctx == 'attr':
        return ''.join(p[1] for p in encode_plain(segs, o, source, attr_quote=occ.tags.get('attr_quote') or '"'))
    if occ.ctx == 'js':
        return js_escape(plain(segs), occ.quote)
    if occ.ctx == 'jstpl':
        return ''.join(js_escape(t, '`') if k == 'markup' else t for k, t in encode_plain(segs, o, source))
    if occ.ctx == 'jshtml':
        return ''.join(js_escape(t, occ.quote) if k == 'markup' else t for k, t in encode(segs, o, source))
    if occ.ctx == 'jsattr':
        return ''.join(js_escape(t, occ.quote) if k == 'markup' else t
                       for k, t in encode_plain(segs, o, source, attr_quote=occ.tags.get('attr_quote') or '"'))
    if occ.ctx == 'jscat':
        quote = next((q for kind, q in occ.tags.get('parts', []) if kind == 'str'), "'")
        out, buf = [], []
        for s in segs:
            if s.lock >= 0:
                if buf:
                    out.append(quote + js_escape(''.join(buf), quote) + quote); buf = []
                out.append(occ.locks[s.lock]['src'])
            else:
                buf.append(s.text)
        if buf or not out:
            out.append(quote + js_escape(''.join(buf), quote) + quote)
        return ' + '.join(out)
    if occ.ctx == 'md':
        from .docs import encode_md
        return encode_md(segs, o, source)
    raise ValueError('unknown context ' + occ.ctx)


# ------------------------------------------------------------------ fallback text the script replaces on load

def plan_derived(text, changes):
    """Fallback text in index.html that the script replaces on load, for each edited message that writes it:
    [(start, end, new_text, note)] in the coordinates of `text`."""
    import html as _h
    from .pages import REPORT_DERIVED
    out = []
    for marker, signature in REPORT_DERIVED:
        ch = next((c for c in changes if any(o.tags.get('sink') == signature for o in c.unit.occs)), None)
        if ch is None or marker not in text:
            continue
        a = text.index(marker) + len(marker)
        b = text.index('<', a)
        shown = _h.unescape(text[a:b])
        parts = re.split(r'\{\d+\}', ch.unit.text)
        m = re.fullmatch('(.+?)'.join(re.escape(p) for p in parts), shown)
        if not m:
            out.append((a, b, None, 'The fallback text “%s” in index.html could not be updated to match the new wording; update it by hand.' % shown))
            continue
        new = plain(ch.segs)
        values = list(m.groups())
        # {n} in the new text follow the reading order of the values; map them back by expression source
        olds = [s for s in ch.unit.segs if s.lock >= 0]
        news = [s for s in ch.segs if s.lock >= 0]
        by_src = {}
        occ = next(o for o in ch.unit.occs if o.tags.get('sink') == signature)
        for k, s in enumerate(olds):
            by_src[occ.locks[s.lock]['src']] = values[k] if k < len(values) else ''
        for s in news:
            new = new.replace(s.text, by_src.get(occ.locks[s.lock]['src'], s.text), 1)
        out.append((a, b, new, None))
    return out


def sync_derived(text, changes):
    notes = []
    for a, b, new, note in sorted(plan_derived(text, changes), key=lambda d: -d[0]):
        if note:
            notes.append(note)
            continue
        text = text[:a] + new.replace('&', '&amp;').replace('<', '&lt;') + text[b:]
    return text, notes


# ------------------------------------------------------------------ checks

def check_scripts(path, text):
    """Parse every inline script with Node (when it is installed) so a broken string is caught before publishing."""
    node = shutil.which('node')
    if not node or not path.endswith('.html'):
        return []
    errors = []
    for m in re.finditer(r'<script>(.*?)</script>', text, re.S):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(m.group(1))
            name = f.name
        try:
            r = subprocess.run([node, '--check', name], capture_output=True, text=True)
            if r.returncode != 0:
                lines = [l.strip() for l in (r.stderr or '').splitlines() if l.strip()]
                msg = next((l for l in lines if 'Error' in l), lines[0] if lines else 'syntax error')
                errors.append('%s: the page script would not run (%s).' % (path, msg))
        finally:
            os.unlink(name)
    return errors


def file_quote_style(path, text):
    if path.endswith('.md'):
        return quote_style(re.sub(r'```.*?```|`[^`]*`', '', text, flags=re.S)) or 'straight'
    return 'curly'


def _attempt(sources, units, changes):
    """Write the changes into copies of the sources and check them. Returns (new_sources, notes, problem) where
    problem is None when everything reads back exactly as intended."""
    edits = collections.defaultdict(list)
    for ch in changes:
        for occ in ch.unit.occs:
            edits[occ.file].append((occ.start, occ.end, encode_occ(ch.segs, occ, sources[occ.file], ch.links)))
    new_sources = dict(sources)
    for f, items in edits.items():
        items.sort(key=lambda e: e[0])
        for x, y in zip(items, items[1:]):
            if x[1] > y[0]:
                return None, [], 'two edits overlap in %s' % f
        text = sources[f]
        for start, end, new in sorted(items, key=lambda e: -e[0]):
            text = text[:start] + new + text[end:]
        new_sources[f] = text
    notes = []
    if 'index.html' in edits or any(o.file == 'index.html' for ch in changes for o in ch.unit.occs):
        new_sources['index.html'], notes = sync_derived(new_sources['index.html'], changes)
    try:
        after = build(new_sources)
    except Exception as e:   # the edited page no longer parses for the tool
        return None, notes, 'the edited files could not be read back (%s)' % e
    expected = collections.Counter({k: len(u.occs) for k, u in units.items()})
    for ch in changes:
        expected[ch.unit.key] -= len(ch.unit.occs)
        occ = ch.unit.occs[0]
        if occ.ctx == 'md':
            from .docs import reread_md
            written = encode_occ(ch.segs, occ, sources[occ.file], ch.links)
            expected[unit_key(by_appearance(reread_md(written, occ.quote)), False, scope_of(occ.file))] += len(ch.unit.occs)
        else:
            expected[ch.key_after] += len(ch.unit.occs)
    if 'index.html' in sources:
        by_key = {ch.unit.key: ch for ch in changes}
        for a, b, new_text, note in plan_derived(sources['index.html'], changes):
            if new_text is None:
                continue
            for u in units.values():
                for o in u.occs:
                    if o.file != 'index.html' or not (o.start <= a and b <= o.end):
                        continue
                    base = by_key[u.key].segs if u.key in by_key else u.segs
                    new_segs = [replace(x, text=' '.join(new_text.split())) if x.lock >= 0 and o.locks[x.lock].get('span')
                                and o.locks[x.lock]['span'][0] <= a and b <= o.locks[x.lock]['span'][1] else x for x in base]
                    expected[unit_key(by_appearance(base), False, scope_of(o.file))] -= 1
                    expected[unit_key(by_appearance(new_segs), False, scope_of(o.file))] += 1
    expected = +expected
    got = collections.Counter({k: len(u.occs) for k, u in after.items()})
    if got != expected:
        return new_sources, notes, 'the text would not read back exactly as written'
    for f in edits:
        errs = check_scripts(f, new_sources[f])
        if errs:
            return new_sources, notes, ' '.join(errs)
    return new_sources, notes, None


# ------------------------------------------------------------------ the whole import

def _norm(t):
    return ' '.join(unicodedata.normalize('NFC', t).split())


def not_a_review(name, why):
    if why == 'strict':
        return ('%s was saved in Word’s “Strict Open XML” format, which this tool cannot read. In Word, use '
                'File > Save As > Word Document (.docx), and import that file.' % name)
    return '%s is not a Word document (.docx). Save it as .docx and import it again.' % name


def run_import(root, docx_paths, dry_run=False, report_dir=None):
    from .export import left_texts, site_info
    home, _ = site_info(root)
    files = all_files()
    sources = read_sources(root, files)
    units = build(sources)
    styles = {f: file_quote_style(f, t) for f, t in sources.items()}
    changes, skipped, loose, kept, problems, docs_read = [], [], [], [], [], []
    untouched_stale, outside_notes, missing_rows = [], [], []
    by_key = {}

    def skip(unit, key, reason, text='', comments=(), where=None):
        occ = unit.occs[0] if unit else None
        skipped.append(Skipped(where or (occ.where if occ else 'ID %s' % key), key, reason, text, list(comments),
                               occ.file if occ else '', unit.text if unit else ''))

    for dp in docx_paths:
        name = pathlib.Path(dp).name
        try:
            review = docx_in.read(dp)
        except docx_in.NotAReviewDocument as e:
            problems.append(not_a_review(name, str(e)))
            continue
        except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError) as e:
            problems.append('%s could not be read as a Word document (%s). Save it as .docx and import it again.' % (name, e))
            continue
        keyed = [r for r in review.rows if r.key]
        docs_read.append((name, len(keyed)))
        if not keyed:
            problems.append('No review rows were found in %s. Its table was turned into plain text (TextEdit does this) or '
                            'lost its shape, and saving it again cannot bring the table back. Ask the writer to send the '
                            'document again from Word, Pages, or Google Docs, or copy the edits into a fresh export.' % name)
            continue
        if review.ids_outside_tables:
            outside_notes.append('%s: %s outside the review table (the first one reads \u201c%s\u201d), so %s could not be read; '
                                 'make those edits by hand.' % (name, plural(review.ids_outside_tables, 'row ID is', 'row IDs are'),
                                                                review.first_outside, 'it' if review.ids_outside_tables == 1 else 'they'))
        loose.extend(dict(c, doc=name) for c in review.loose_comments)
        texts = collections.defaultdict(list)
        for r in keyed:
            t = _norm(docx_in.text_of(r.paragraphs))
            if t not in texts[r.key]:
                texts[r.key].append(t)
        dupes = {k for k, v in texts.items() if len(v) > 1}
        dupe_comments = collections.defaultdict(list)
        for r in keyed:
            if r.key in dupes:
                dupe_comments[r.key].extend(r.comments)
        reported_dupes = set()
        present = {r.key for r in keyed}
        stale = [r for r in keyed if r.key not in units]
        doc_kind = 'report' if any(units[r.key].occs[0].file in REPORT_FILES for r in keyed if r.key in units) else 'docs'
        for row in review.rows:
            text = docx_in.text_of(row.paragraphs)
            if row.header:
                loose.extend(dict(c, doc=name) for c in row.comments)
                continue
            if row.key is None:
                if text and any(_norm(text) == _norm(u.text) for u in units.values()):
                    loose.extend(dict(c, doc=name) for c in row.comments)   # its ID line was damaged, but nothing changed
                    continue
                if text:
                    skipped.append(Skipped('A row without an ID, in %s' % name, '', 'This row has no ID (its ID line was deleted '
                                           'or changed, or the row was added), so its text could not be placed on the page. '
                                           'Make this change by hand if it is wanted.', text, row.comments))
                else:
                    loose.extend(dict(c, doc=name) for c in row.comments)
                continue
            unit = units.get(row.key)
            if row.inserted:
                skip(unit, row.key, 'This row was added by copying another row, so its text has no place on the page. '
                                    'Add the text by hand if it is wanted.', text, row.comments)
                continue
            if row.key in dupes:
                if row.key not in reported_dupes:
                    reported_dupes.add(row.key)
                    versions = ' / '.join('\u201c%s\u201d' % t for t in texts[row.key])
                    skip(unit, row.key, 'The ID %s appears in %s of %s with different text (was a row copied?), so no '
                                        'version was applied. The rows say: %s.' % (row.key, plural(len(texts[row.key]), 'row'), name, versions),
                         '', dupe_comments[row.key])
                continue
            if row.shape:
                note = (' The extra text was: “%s”.' % row.extra_text) if row.extra_text else ''
                skip(unit, row.key, 'The table changed shape in this row (%s), so its text could not be read safely.%s '
                                    'Make this change by hand.' % (row.shape, note), text, row.comments)
                continue
            if unit is None:
                place = re.sub(r'ID\s*[0-9a-f]{8}(\s*\u00b7\s*[0-9a-f]{8})?', '', row.left).strip().split('\n')[0] or 'ID %s' % row.key
                if row.text_hash and text_fingerprint(text) == row.text_hash and not row.deleted:
                    untouched_stale.append(place)
                    loose.extend(dict(c, doc=name) for c in row.comments)
                    continue
                skipped.append(Skipped('%s (ID %s, in %s)' % (place, row.key, name), row.key,
                                       'The writer edited text that has changed on the page since the document was exported, '
                                       'so the edit was not applied. Compare the two versions and merge them by hand; the page '
                                       'has the newer text.' if row.text_hash else
                                       'This text has changed on the page since the document was exported (or the ID was '
                                       'edited), so the edit was not applied. Compare it with the page and make the change by hand.',
                                       text, row.comments))
                continue
            if row.deleted:
                skip(unit, row.key, 'The writer deleted this row. The import does not take text off the page; if it should '
                                    'go, remove it by hand.', text, row.comments)
                continue
            export_files = REPORT_FILES if unit.occs[0].file in REPORT_FILES else [f for f in files if f not in REPORT_FILES]
            left_note = _norm(re.sub(r'ID\s*[0-9a-f]{8}(\s*\u00b7\s*[0-9a-f]{8})?', '', row.left))
            exported_left = _norm(' '.join(left_texts(unit, export_files)))
            comments = list(row.comments)
            if row.extra_text:
                comments.append({'author': 'Writer (in an added column)', 'text': row.extra_text})
            if left_note and left_note.replace(' ', '') != exported_left.replace(' ', ''):
                comments.append({'author': 'Writer (in the left column)', 'text': re.sub(r'ID\s*[0-9a-f]{8}(\s*\u00b7\s*[0-9a-f]{8})?', '', row.left).strip()})
            if unit.locked:
                if _norm(text) != _norm(unit.text):
                    skip(unit, row.key, 'This text is shown for context and kept exactly as it is, so the edit was not applied.', text, comments)
                elif comments:
                    kept.append((unit, comments))
                continue
            try:
                segs, links, warns, err = read_edit(unit, row, styles, home)
            except Exception as e:   # one unreadable row never stops the import
                skip(unit, row.key, 'This row could not be read (%s: %s). Make this change by hand.' % (type(e).__name__, e), text, comments)
                continue
            if err:
                skip(unit, row.key, err, text, comments)
                continue
            if canon(segs) == canon(unit.segs) and links == link_list(unit):
                lost = [w for w in warns if re.search(r'dropped|ignored|removed|left out', w)]
                if lost:
                    skip(unit, row.key, 'Only formatting changed, and it cannot be shown here: ' + ' '.join(lost), text, comments)
                elif comments:
                    kept.append((unit, comments))
                continue
            prev = by_key.get(row.key)
            if prev is not None:
                if isinstance(prev, Skipped):
                    prev.comments.extend(comments)
                    prev.reason = prev.reason.replace('; neither edit was applied', '; none of the edits was applied')
                    continue
                if canon(prev.segs) == canon(segs) and prev.links == links:
                    prev.comments.extend(comments)
                    continue
                changes.remove(prev)
                sk = Skipped(unit.occs[0].where, row.key, 'Edited differently in %s (“%s”) and %s (“%s”); '
                             'neither edit was applied.' % (prev.source_doc, plain(prev.segs), name, plain(segs)),
                             '', prev.comments + comments, unit.occs[0].file, unit.text)
                skipped.append(sk)
                by_key[row.key] = sk
                continue
            ch = Change(unit, segs, links, warns, comments, name)
            by_key[row.key] = ch
            changes.append(ch)
        if not stale:
            from .export import exported_keys
            gone = [k for k in exported_keys(sources, units, doc_kind) if k not in present]
            if gone:
                missing_rows.append((name, [units[k].occs[0].where for k in gone]))

    new_sources, derived_notes, written = dict(sources), [], []
    if not problems and changes:
        pending = list(changes)
        while pending:
            result, notes, problem = _attempt(sources, units, pending)
            if problem is None:
                new_sources, derived_notes = result, notes
                break
            # find the edits that cause it, one by one, and set them aside
            culprits = []
            for ch in pending:
                _, _, p1 = _attempt(sources, units, [ch])
                if p1 is not None:
                    culprits.append((ch, p1))
            if not culprits:
                problems.append('The edits could not be written together (%s); nothing was written.' % problem)
                break
            for ch, why in culprits:
                pending.remove(ch)
                changes.remove(ch)
                skip(ch.unit, ch.unit.key, 'This edit was set aside because %s. Make it by hand. The writer’s version: '
                                           '“%s”.' % (why, plain(ch.segs)), plain(ch.segs), ch.comments)
        else:
            new_sources = dict(sources)
    to_write = [f for f in files if new_sources.get(f) != sources.get(f)]
    dirty = uncommitted(root, to_write) if to_write else []
    if not problems and not dry_run:
        for f in files:
            if new_sources.get(f) != sources.get(f):
                with open(pathlib.Path(root) / f, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(new_sources[f])
                written.append(f)
    report = make_report(changes if not problems else [], skipped, loose, kept, problems, derived_notes, written,
                         dry_run, docx_paths, docs_read, untouched_stale, outside_notes, missing_rows, dirty)
    report_path = None
    if report_dir is not None:
        pathlib.Path(report_dir).mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime('%Y-%m-%d-%H%M%S')
        base = ('preview-report-%s' if dry_run else 'import-report-%s') % stamp
        report_path = pathlib.Path(report_dir) / (base + '.md')
        n = 2
        while report_path.exists():
            report_path = pathlib.Path(report_dir) / ('%s-%d.md' % (base, n)); n += 1
        report_path.write_text(report, encoding='utf-8')
    n_comments = (len(loose) + sum(len(c.comments) for c in changes) + sum(len(c[1]) for c in kept)
                  + sum(len(s.comments) for s in skipped))
    return {'changes': changes if not problems else [], 'skipped': skipped, 'problems': problems, 'written': written,
            'report': report, 'report_path': str(report_path) if report_path else None, 'new_sources': new_sources,
            'comments': n_comments, 'docs_read': docs_read, 'dirty': dirty, 'untouched_stale': untouched_stale}


def uncommitted(root, files):
    """Files among these that already have uncommitted changes (when the folder is a Git repository)."""
    git = shutil.which('git')
    if not git:
        return []
    try:
        r = subprocess.run([git, 'status', '--porcelain', '--'] + list(files), cwd=str(root), capture_output=True, text=True)
    except OSError:
        return []
    if r.returncode != 0:
        return []
    return [line[3:].strip() for line in r.stdout.splitlines() if line[:2].strip()]


# ------------------------------------------------------------------ the report

def word_diff(old, new):
    a, b = re.findall(r'\S+|\s+', old), re.findall(r'\S+|\s+', new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal':
            out.append(''.join(a[i1:i2]))
            continue
        for chunk, mark in ((''.join(a[i1:i2]), '~~'), (''.join(b[j1:j2]), '**')):
            if not chunk:
                continue
            core = chunk.strip()
            if not core:
                out.append(chunk) if mark == '**' else None
                continue
            lead = chunk[:len(chunk) - len(chunk.lstrip())]
            trail = chunk[len(chunk.rstrip()):]
            out.append(lead + mark + core + mark + trail)
    return re.sub(r'  +', ' ', ''.join(out))


def original_numbers(segs, unit):
    """Show each moved value with the number it had in the document, so the diff shows a move, not a change."""
    shown = {s.lock: s.text for s in unit.segs if s.lock >= 0}
    return [replace(s, text=shown.get(s.lock, s.text)) if s.lock >= 0 else s for s in segs]


def describe_formatting(old_segs, new_segs, old_links, new_links):
    """Bold or italic added to or removed from words that stayed the same (changed words show in the diff)."""
    def flags(segs, flag):
        out = []
        for s in segs:
            out.extend([bool(getattr(s, flag)) and s.lock < 0] * len(s.text))
        return out
    old_text, new_text = plain(old_segs), plain(new_segs)
    notes = []
    blocks = difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).get_matching_blocks()
    for flag in ('bold', 'italic'):
        a, b = flags(old_segs, flag), flags(new_segs, flag)
        gone, added = [], []
        for i0, j0, size in blocks:
            run_g, run_a = '', ''
            for k in range(size):
                ch = old_text[i0 + k]
                if a[i0 + k] and not b[j0 + k]:
                    run_g += ch
                elif run_g:
                    gone.append(run_g); run_g = ''
                if b[j0 + k] and not a[i0 + k]:
                    run_a += ch
                elif run_a:
                    added.append(run_a); run_a = ''
            if run_g:
                gone.append(run_g)
            if run_a:
                added.append(run_a)
        gone = [g.strip() for g in gone if g.strip()]
        added = [g.strip() for g in added if g.strip()]
        if gone:
            notes.append('%s removed from %s' % (flag, ', '.join('\u201c%s\u201d' % t for t in gone)))
        if added:
            notes.append('%s added to %s' % (flag, ', '.join('\u201c%s\u201d' % t for t in added)))
    return notes


def make_report(changes, skipped, loose, kept, problems, derived_notes, written, dry_run, docx_paths, docs_read,
                untouched_stale=(), outside_notes=(), missing_rows=(), dirty=()):
    L = []
    L.append('# Text review import, %s' % _dt.datetime.now().strftime('%B %d, %Y, %H:%M').replace(' 0', ' '))
    L.append('')
    for name, n in docs_read:
        L.append('- %s: %s read' % (name, plural(n, 'row')))
    L.append('')
    places = sum(len(c.unit.occs) for c in changes)
    if problems:
        L.append('**Nothing was written.**')
        L.extend('- ' + p for p in problems)
    elif dry_run:
        L.append('**Preview only: no files were changed.** %s would be written to %s.' % (plural(len(changes), 'edit'), plural(places, 'place')))
    else:
        L.append('%s written to %s%s.' % (plural(len(changes), 'edit'), plural(places, 'place'),
                                          (' in ' + ', '.join('`%s`' % f for f in written)) if written else ''))
    if skipped:
        L.append('%s not applied; see \u201cNot applied\u201d below.' % plural(len(skipped), 'row'))
    elif docs_read and not problems and not outside_notes:
        L.append('Every edited row was applied.')
    if dirty and not problems:
        L.append('')
        L.append('**Note:** %s already had uncommitted changes before this import; they are now mixed with the writer\u2019s '
                 'edits. Review with `git diff` before committing.' % ', '.join('`%s`' % f for f in dirty))
    L.append('')
    if changes:
        L.append('## Changes')
        L.append('')
        L.append('Removed words are ~~struck through~~ and added words are **bold**.')
        L.append('')
        for c in changes:
            occ = c.unit.occs[0]
            L.append('### %s' % occ.where)
            L.append('ID %s. Written to: %s.' % (c.unit.key, '; '.join('`%s` (%s)' % (o.file, o.where) for o in c.unit.occs)))
            L.append('')
            old, new = plain(c.unit.segs), plain(original_numbers(c.segs, c.unit))
            L.append(word_diff(old, new) if old != new else 'The words are unchanged.')
            fmt = describe_formatting(c.unit.segs, c.segs, link_list(c.unit), c.links)
            if fmt:
                L.append('')
                L.append('> Formatting: ' + '; '.join(fmt) + '. Check `git diff --word-diff`.')
            for w in c.warnings:
                L.append('')
                L.append('> Note: ' + w)
            for cm in c.comments:
                L.append('')
                L.append('> Comment from %s: %s' % (cm.get('author') or 'the writer', cm.get('text')))
            L.append('')
    if skipped:
        L.append('## Not applied')
        L.append('')
        for s in skipped:
            L.append('### %s' % s.where)
            meta = ', '.join(x for x in (('`%s`' % s.file) if s.file else '', ('ID %s' % s.key) if s.key else '') if x)
            if meta:
                L.append(meta + '.')
            L.append(s.reason)
            if s.was:
                L.append('')
                L.append('> Now on the page: ' + s.was[:600])
            if s.text and _norm(s.text) != _norm(s.was):
                L.append('')
                L.append('> The writer’s version: ' + s.text[:600])
            for cm in s.comments:
                L.append('')
                L.append('> Comment from %s: %s' % (cm.get('author') or 'the writer', cm.get('text')))
            L.append('')
    if kept or loose:
        L.append('## Comments on text that did not change')
        L.append('')
        for unit, cms in kept:
            for cm in cms:
                L.append('- **%s** (`%s`, ID %s), on “%s”. %s: %s' % (
                    unit.occs[0].where, unit.occs[0].file, unit.key, unit.text[:120], cm.get('author') or 'The writer', cm.get('text')))
        for cm in loose:
            L.append('- In %s, %s: %s' % (cm.get('doc', 'the document'), cm.get('author') or 'the writer', cm.get('text')))
        L.append('')
    if outside_notes or missing_rows or untouched_stale:
        L.append('## Also worth knowing')
        L.append('')
        for n in outside_notes:
            L.append('- ' + n)
        for name, places in missing_rows:
            L.append('- %s is missing %s that the export had (deleted without Track Changes?); nothing on the page was '
                     'changed for them: %s%s.' % (name, plural(len(places), 'row'), '; '.join(places[:12]),
                                                   ' and %d more' % (len(places) - 12) if len(places) > 12 else ''))
        if untouched_stale:
            L.append('- %s changed on the page after the export; the writer did not edit them, so nothing was needed: %s.' % (
                plural(len(untouched_stale), 'row'), '; '.join(untouched_stale[:12])))
        L.append('')
    if derived_notes:
        L.append('## Fallback text')
        L.extend('- ' + n for n in derived_notes)
        L.append('')
    if written:
        L.append('## Next steps')
        L.append('')
        L.append('1. Look at the pages locally: `npm start`, then open http://localhost:8080.')
        if dirty:
            L.append('2. Review the edits with `git diff --word-diff`. Some of these files had your own uncommitted changes '
                     'too, so undo by hand if needed rather than with `git checkout`.')
        else:
            L.append('2. Review the edits: `git diff --word-diff`. To undo them all: `git checkout -- %s`.' % ' '.join(written))
        L.append('3. Publish: `git add %s`, then `git commit -m "docs: apply copy edits from the text review"` and `git push`.' % ' '.join(written))
        L.append('')
    return '\n'.join(L)
