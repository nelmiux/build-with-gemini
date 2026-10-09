"""Collect the text of every published file into units: identical text in several places becomes one unit,
so an edit is made once and written to every place."""
from __future__ import annotations

import hashlib
import pathlib

from .model import Unit, canon
from .pages import REPORT_SECTIONS, report_pieces

REPORT_FILES = ['index.html', '404.html']
IDENTIFIER_RES = [
    r'(?![\w.-]+:$)[\w.@/:-]*[_/@:][\w.@/:-]*',    # customer_data, roles/x, test@..., principal://... (not 'Boxes:')
    r'[a-z0-9]+(?:-[a-z0-9]+){2,}',                 # test-agent-caller, promo-agent-shadow
    r'HTTP \d{3}\b.*',                               # recorded results
    r'[A-Z][A-Z_]{3,}(?: \(.+\))?',                  # PASSED, REMEDIATED
    r'M\d(?: \(.+\))?', r'Case \d+', r'Reasoning Engine [\d.]+',
]


def read_sources(root, files):
    """Read files exactly as they are, line endings included (they are written back the same way)."""
    out = {}
    for f in files:
        with open(pathlib.Path(root) / f, encoding='utf-8', newline='') as fh:
            out[f] = fh.read()
    return out


def scope_of(path):
    """Identical text is one unit only within one surface: a nav link is not a table heading in a document."""
    return path if path.endswith('.html') else 'markdown'


def identifier_like(text):
    import re
    return any(re.fullmatch(rx, text) for rx in IDENTIFIER_RES)


def all_files():
    from . import docs   # noqa: imported late: the docs part is optional
    return REPORT_FILES + docs.DOCS_FILES


def pieces_for(path, text):
    if path == 'index.html':
        return report_pieces(text, path)
    if path == '404.html':
        from .pages import simple_page_pieces
        return simple_page_pieces(text, path)
    from . import docs
    return docs.pieces(path, text)


def build(sources):
    """All units across the given sources, keyed by fingerprint."""
    units = {}
    for path, text in sources.items():
        for p in pieces_for(path, text):
            if not p.occ.locked and identifier_like(''.join(s.text for s in p.segs)):
                p.occ.locked = True
                p.occ.note = 'An identifier or a recorded result, kept exactly as it is.'
            key = unit_key(p.segs, p.occ.locked, scope_of(path))
            if key in units:
                units[key].occs.append(p.occ)
            else:
                units[key] = Unit(key, p.segs, [p.occ])
    return units


def text_fingerprint(text):
    """The words alone (no formatting), so an untouched row can be recognised even after the page changed."""
    import unicodedata
    t = ' '.join(unicodedata.normalize('NFC', text).replace('\u00a0', ' ').split())
    return hashlib.sha1(t.encode('utf-8')).hexdigest()[:8]


def unit_key(segs, locked=False, scope=''):
    return hashlib.sha1((canon(segs) + ('|fixed' if locked else '') + '|' + scope).encode('utf-8')).hexdigest()[:8]


def section_rank(path, section):
    if path == '404.html':
        return 100
    if path == 'index.html':
        names = [s for s, _ in REPORT_SECTIONS]
        return names.index(section) if section in names else len(names)
    from . import docs
    return docs.section_rank(path, section)


def sort_key(unit, files):
    occ = min((o for o in unit.occs if o.file in files),
              key=lambda o: (section_rank(o.file, o.section), files.index(o.file), o.order))
    return (section_rank(occ.file, occ.section), files.index(occ.file), occ.order), occ
