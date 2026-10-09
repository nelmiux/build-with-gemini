"""Write the review document for the report page, or for the docs page and its documents."""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
from urllib.parse import urljoin

from .docx_out import Doc, FAINT, GRAY, ID_PREFIX, LIGHT, LINK, LOCK_FILL
from .units import REPORT_FILES, all_files, build, read_sources, sort_key

def exported_keys(sources, units, which):
    """The IDs an export of this kind ('report' or 'docs') contains, given the current sources."""
    files = REPORT_FILES if which == 'report' else __import__('textreview.docs', fromlist=['DOCS_FILES']).DOCS_FILES
    return [u.key for u in units.values() if any(o.file in files for o in u.occs)]


def long_date(d):
    return '%s %d, %d' % (d.strftime('%B'), d.day, d.year)


def site_info(root):
    pkg = json.loads((pathlib.Path(root) / 'package.json').read_text(encoding='utf-8'))
    home = pkg.get('homepage') or ''
    if home and not home.endswith('/'):
        home += '/'
    return home, pkg.get('author') or ''


def link_target(href, occ, home):
    if href.startswith(('http://', 'https://', 'mailto:')):
        return href
    if occ.file.endswith('.md'):
        from . import docs
        return docs.md_link_target(href, occ.file, home)
    return urljoin(home + (occ.file if occ.file != 'index.html' else ''), href)


def _context(o):
    """A short prefix that says which page part or document a place belongs to."""
    if o.file.endswith('.md'):
        from . import docs
        return docs.DOC_TITLES.get(o.file, o.file) + ' \u00b7 '
    if o.group.startswith('Slide '):
        return o.group + ' \u00b7 '
    if o.file == '404.html':
        return 'Page-not-found page \u00b7 '
    return ''


def where_label(unit, files):
    here = [o for o in unit.occs if o.file in files]
    elsewhere = [o for o in unit.occs if o.file not in files]
    several_docs = len({o.file for o in here}) > 1
    labels = []
    for o in here:
        prefix = _context(o) if (several_docs if o.file.endswith('.md') else len(here) > 1) else ''
        if prefix + o.where not in labels:
            labels.append(prefix + o.where)
    text = labels[0]
    if len(labels) > 1:
        rest = len(labels) - 2
        text = '; '.join(labels[:2]) + ('' if rest <= 0 else ' (and %d more place%s)' % (rest, '' if rest == 1 else 's'))
    elif len(here) > 1:
        text += ' (%d places)' % len(here)
    if elsewhere:
        pages = sorted({('the report page' if o.file == 'index.html' else 'the docs page' if o.file == 'docs.html' else o.file) for o in elsewhere})
        text += '. Also used on ' + ' and '.join(pages) + '; one edit changes all of them'
    return text


def left_texts(unit, files):
    """What the left-hand cell says, apart from the ID: the place, then any notes."""
    notes = []
    for o in unit.occs:
        if o.note and o.note not in notes:
            notes.append(o.note)
    if unit.locked:
        notes.insert(0, 'Shown for context; not editable.')
    return [where_label(unit, files)] + notes


def right_cell(doc, unit, occ, home):
    from .htmlfrag import is_placeholder
    runs = []
    locked = unit.locked
    groups = []
    for s in unit.segs:
        if groups and groups[-1][0] == s.link:
            groups[-1][1].append(s)
        else:
            groups.append((s.link, [s]))
    for link, segs in groups:
        href = occ.links[link].get('href') or '' if 0 <= link < len(occ.links) else ''
        usable = link >= 0 and href and not any(is_placeholder(c) for c in href)
        rr = []
        for s in segs:
            if s.lock >= 0:
                rr.append(doc.run(s.text, bold=s.bold, italic=s.italic, shade=LOCK_FILL, color=GRAY))
            else:
                rr.append(doc.run(s.text, bold=s.bold, italic=s.italic or locked, mono=s.code,
                                  color=(LINK if usable else (LIGHT if locked else None)), underline=bool(usable)))
        if usable:
            rid = doc.link_rid(link_target(href, occ, home))
            runs.append('<w:hyperlink r:id="%s" w:history="1">%s</w:hyperlink>' % (rid, ''.join(rr)))
        else:
            runs.extend(rr)
    return [doc.para(runs, after=0)]


def left_cell(doc, unit, files):
    texts = left_texts(unit, files)
    paras = [doc.para([doc.run(texts[0], size=18, color=GRAY)], after=40)]
    for n in texts[1:]:
        paras.append(doc.para([doc.run(n, italic=True, size=17, color=GRAY)], after=40))
    from .units import text_fingerprint
    paras.append(doc.para([doc.run('%s%s \u00b7 %s' % (ID_PREFIX, unit.key, text_fingerprint(unit.text)), size=14, color=FAINT)], after=0))
    return paras


INSTRUCTIONS = [
    'The site is a field report for colleagues who did not attend the workshop. Aim for clear, plain, professional text that someone outside the team can follow.',
    'Edit only the right-hand column; anything else in this document is ignored. Each row is one piece of text from the site. Leave the left-hand column as it is: the ID at its bottom is how your edit finds its place.',
    'Word: Track Changes is already on. Google Docs: switch to Suggesting before you start. Plain edits work too.',
    'When you are done, send this file back as a Word document (.docx). From Google Docs, use File > Download > Microsoft Word (.docx); a shared link or a PDF cannot be imported.',
    'Text with a gray background must stay exactly as it is: {1}, {2}, and so on stand for values the page fills in, such as a number or a name, and other text with a gray background is a name or code. You may move it within a sentence, but do not change, delete, or repeat it.',
    'Blue underlined words are links. You can reword them and the link stays, unless the row\u2019s note says to keep certain words exactly: those links appear only when the exact words are kept.',
    'Bold and italic carry over where the page shows them. In plain rows such as tooltips and buttons, formatting is ignored.',
    'Some rows are used in several places; the left column says so, and one edit changes all of them.',
    'Keep each piece to one paragraph. Do not add, copy, or delete rows; to suggest new text, a move, or a cut, add a comment.',
    'Short labels (buttons, tooltips, table headings) need to stay short. Rows marked \u201cread aloud by screen readers\u201d are heard rather than seen, so write them to be listened to.',
    'Keep facts, numbers, names, and technical terms as they are. If one looks wrong, add a comment rather than changing it.',
    'The text follows American English, with the serial comma and with commas and periods inside closing quotation marks.',
    'Rows marked \u201cShown for context\u201d cannot be changed; they are there so that the rows around them make sense. Italic notes under a place name are guidance for that row.',
]
WHERE_TO_START = {
    'report': 'Most of the prose is in Overview, 1 \u00b7 The scenario, 4 \u00b7 Assessment and recommendation, and The slides; the diagram and interface rows are short labels. Commands, recorded outputs, and a few words that come from styling are not included; comment on the nearest row about them.',
    'docs': 'Most of the prose is in Project Overview and the mission documents (M0 to M5). The two earlier drafts are for proofreading only, and the last section holds the docs page\u2019s short labels and messages. Diagram labels and code blocks are not included; to change a diagram\u2019s wording, add a comment on the row nearest to it.',
}


def export(root, out_path, which='report'):
    home, author = site_info(root)
    files = REPORT_FILES if which == 'report' else __import__('textreview.docs', fromlist=['DOCS_FILES']).DOCS_FILES
    sources = read_sources(root, all_files())
    units = build(sources)
    chosen = [u for u in units.values() if any(o.file in files for o in u.occs)]
    chosen.sort(key=lambda u: sort_key(u, files)[0])
    editable = sum(1 for u in chosen if not u.locked)
    words = sum(len(u.text.split()) for u in chosen if not u.locked)

    doc = Doc()
    if which == 'report':
        title = 'Text review: the report page and slides'
        views = [(home, home), (home + '#present', 'the slides')]
    else:
        title = 'Text review: the documentation page and its documents'
        views = [(home + 'docs.html', home + 'docs.html')]

    def link_run(url, text):
        return '<w:hyperlink r:id="%s" w:history="1">%s</w:hyperlink>' % (doc.link_rid(url), doc.run(text, color=LINK, underline=True))

    doc.add(doc.para([doc.run(title)], style='Title'))
    doc.add(doc.para([doc.run('Exported on %s, from the site\u2019s source: %s of text, about %s words. Where the live site '
                              'and this document differ, this document has the current text.' % (
                                  long_date(_dt.date.today()), '{:,} pieces'.format(editable), '{:,}'.format(words)), color=GRAY)], after=60))
    view_runs = [doc.run('To see each piece in place, open ', color=GRAY)]
    for k, (url, text) in enumerate(views):
        if k:
            view_runs.append(doc.run(' (or ', color=GRAY))
        view_runs.append(link_run(url, text))
        if k:
            view_runs.append(doc.run(')', color=GRAY))
    view_runs.append(doc.run('. ' + WHERE_TO_START[which], color=GRAY))
    doc.add(doc.para(view_runs, after=200))
    doc.heading('How to review this document', 1)
    for line in INSTRUCTIONS:
        doc.bullet([doc.run(line)])

    current = None
    rows = []
    group = None

    def flush():
        if rows:
            doc.table(list(rows))
            rows.clear()

    for u in chosen:
        key, occ = sort_key(u, files)
        sec = (occ.file, occ.section)
        if sec != current:
            flush()
            current, group = sec, None
            doc.heading(section_title(occ), 1)
            intro = section_intro(occ, home)
            if intro:
                doc.add(doc.para([doc.run(intro, color=GRAY, size=18)], after=120))
        if occ.group != group:
            if occ.group:
                rows.append((None, None, occ.group))
            elif group:
                rows.append((None, None, 'More in this section'))
            group = occ.group
        rows.append((left_cell(doc, u, files), right_cell(doc, u, occ, home), None))
    flush()
    doc.save(out_path, title, author)
    return {'path': str(out_path), 'pieces': editable, 'words': words, 'files': files}


def section_title(occ):
    if occ.file in ('index.html', '404.html'):
        from .pages import REPORT_SECTIONS
        return dict(REPORT_SECTIONS).get(occ.section, occ.section)
    from . import docs
    return docs.section_title(occ.file, occ.section)


def section_intro(occ, home):
    if occ.file == '404.html':
        return 'The page people see when an address on the site does not exist.'
    if occ.file == 'index.html':
        anchors = {'overview': '#overview', 'scenario': '#scenario', 'architecture': '#architecture', 'evidence': '#evidence',
                   'recommendation': '#recommendation', 'present': '#present', 'slides': '#present/1', 'deck': '#present'}
        if occ.section in anchors:
            return 'On the page: %s%s' % (home, anchors[occ.section])
        return ''
    from . import docs
    return docs.section_intro(occ.file, occ.section, home)
