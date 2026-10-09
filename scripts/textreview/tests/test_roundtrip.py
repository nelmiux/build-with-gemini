"""Tests for the writer review tool. Run: python3 -m unittest discover -s scripts/textreview/tests -t scripts"""
import collections
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))

from textreview.apply import encode_occ, run_import            # noqa: E402
from textreview.export import export                           # noqa: E402
from textreview.jslex import js_escape, js_unescape            # noqa: E402
from textreview.units import all_files, build, read_sources    # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def run(text, props=''):
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (('<w:rPr>%s</w:rPr>' % props) if props else '', _esc(text))


def para(*items):
    return '<w:p>%s</w:p>' % ''.join(items)


class Doc:
    """A review document opened for editing, as a writer's editor would save it."""

    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self.parts = {n: z.read(n) for n in z.namelist()}
        self.xml = self.parts['word/document.xml'].decode('utf-8')
        self.rels = self.parts['word/_rels/document.xml.rels'].decode('utf-8')

    def row_span(self, key):
        m = re.search(r'ID %s(?: \u00b7 [0-9a-f]{8})?</w:t>' % key, self.xml)
        start = self.xml.rfind('<w:tr>', 0, m.start())
        end = self.xml.index('</w:tr>', m.end()) + len('</w:tr>')
        return start, end

    def set_right(self, key, cell_xml):
        start, end = self.row_span(key)
        row = self.xml[start:end]
        second = [x.start() for x in re.finditer('<w:tc>', row)][1]
        body = row[second:]
        body = body[:body.index('</w:tcPr>') + len('</w:tcPr>')]
        self.xml = self.xml[:start] + row[:second] + body + cell_xml + '</w:tc></w:tr>' + self.xml[end:]

    def set_text(self, key, text):
        self.set_right(key, para(run(text)))

    def row(self, key):
        start, end = self.row_span(key)
        return self.xml[start:end]

    def link(self, url, text, props=''):
        rid = 'rIdT%d' % (self.rels.count('rIdT') + 1)
        self.rels = self.rels.replace('</Relationships>', '<Relationship Id="%s" Type="http://schemas.openxmlformats.org/'
                                      'officeDocument/2006/relationships/hyperlink" Target="%s" TargetMode="External"/>'
                                      '</Relationships>' % (rid, url))
        return '<w:hyperlink r:id="%s">%s</w:hyperlink>' % (rid, run(text, props))

    def save(self):
        self.parts['word/document.xml'] = self.xml.encode('utf-8')
        self.parts['word/_rels/document.xml.rels'] = self.rels.encode('utf-8')
        with zipfile.ZipFile(self.path, 'w', zipfile.ZIP_DEFLATED) as z:
            for n, d in self.parts.items():
                z.writestr(n, d)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        cls.clean = cls.tmp / 'clean'
        shutil.copytree(ROOT, cls.clean, ignore=shutil.ignore_patterns('.git', 'node_modules', 'text-review', '_site', '__pycache__'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fresh(self, name):
        repo = self.tmp / name
        shutil.copytree(self.clean, repo)
        self.units = build(read_sources(repo, all_files()))
        export(repo, repo / 'r.docx', 'report')
        export(repo, repo / 'd.docx', 'docs')
        return repo

    def key(self, prefix, file=None):
        hits = [u for u in self.units.values() if u.text.startswith(prefix) and (file is None or u.occs[0].file == file)]
        self.assertEqual(len(hits), 1, prefix)
        return hits[0]

    def texts(self, repo):
        return {u.text for u in build(read_sources(repo, all_files())).values()}


class RoundTrip(Base):
    def test_untouched_documents_change_nothing(self):
        repo = self.fresh('untouched')
        res = run_import(repo, [str(repo / 'r.docx'), str(repo / 'd.docx')], dry_run=True)
        self.assertEqual(res['problems'], [])
        self.assertEqual([c.unit.text for c in res['changes']], [])
        self.assertEqual([s.reason for s in res['skipped']], [])

    def test_every_piece_writes_back_as_itself(self):
        src = read_sources(self.clean, all_files())
        units = build(src)
        edits = collections.defaultdict(list)
        for u in units.values():
            for o in u.occs:
                edits[o.file].append((o.start, o.end, encode_occ(u.segs, o, src[o.file])))
        new = {}
        for f, items in edits.items():
            t = src[f]
            for a, b, rep in sorted(items, key=lambda e: -e[0]):
                t = t[:a] + rep + t[b:]
            new[f] = t
        after = build(new)
        self.assertEqual(collections.Counter({k: len(u.occs) for k, u in units.items()}),
                         collections.Counter({k: len(u.occs) for k, u in after.items()}))

    def test_escaping_round_trips(self):
        for q in ("'", '"', '`'):
            for t in ['a</script>b', 'x<!--y', "it's", 'a`b${c}d\\e', 'line\nbreak', 'emoji 🙂 and “quotes”']:
                self.assertEqual(js_unescape(js_escape(t, q))[0], t)

    def test_edits_land_in_every_context(self):
        repo = self.fresh('contexts')
        html, js = self.key('Jump to the recommendation'), self.key('Holds the cost and margin data')
        tpl, md = self.key('Showing {1} of {2} changes'), self.key('Both local servers listen on localhost only.')
        doc = Doc(repo / 'r.docx')
        doc.set_text(html.key, 'Go to the recommendation & more')
        doc.set_text(js.key, "Holds the cost and margin data; it's the back office's call on large discounts.")
        doc.set_text(tpl.key, 'Showing {1} of {2} changes so far')
        doc.save()
        d = Doc(repo / 'd.docx')
        d.set_text(md.key, 'Both local servers listen on localhost only, and ~only~ there, with *stars*. ' + md.text[len('Both local servers listen on localhost only.'):].strip())
        d.save()
        res = run_import(repo, [str(repo / 'r.docx'), str(repo / 'd.docx')])
        self.assertEqual(res['problems'], [])
        self.assertEqual(len(res['changes']), 4, [s.reason for s in res['skipped']])
        texts = self.texts(repo)
        self.assertIn('Go to the recommendation & more', texts)
        self.assertIn('Holds the cost and margin data; it’s the back office’s call on large discounts.', texts)
        self.assertIn('Showing {1} of {2} changes so far', texts)
        self.assertTrue(any(t.startswith('Both local servers listen on localhost only, and ~only~ there, with *stars*.') for t in texts))
        readme = (repo / 'README.md').read_text(encoding='utf-8')
        self.assertIn('\\~only\\~', readme)   # a tilde would otherwise be strikethrough on the docs page

    def test_fallback_text_follows_an_edited_message(self):
        repo = self.fresh('derived')
        k_all, k_fact, line = self.key('All ({1})'), self.key('{1} changes recorded'), self.key('Change log: ')
        doc = Doc(repo / 'r.docx')
        doc.set_text(k_all.key, 'Every change ({1})')
        doc.set_text(k_fact.key, '{1} changes for M1–M2; {2} reversible')
        doc.set_text(line.key, line.text.replace('Change log:', 'Change history:'))
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['problems'], [])
        self.assertEqual(len(res['changes']), 3)
        page = (repo / 'index.html').read_text(encoding='utf-8')
        self.assertIn('aria-pressed="true">Every change (12)</button>', page)
        self.assertIn('Change history: <span id="fact-changes">12 changes for M1–M2; 11 reversible</span>', page)


class Safety(Base):
    def test_a_document_without_its_table_is_refused(self):
        repo = self.fresh('flattened')
        doc = Doc(repo / 'r.docx')
        doc.xml = re.sub(r'<w:tbl>.*?</w:tbl>', lambda m: ''.join(re.findall(r'<w:p>.*?</w:p>', m.group(0))), doc.xml, flags=re.S)
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertTrue(res['problems'])
        self.assertEqual(res['written'], [])

    def test_strict_format_is_refused(self):
        repo = self.fresh('strict')
        doc = Doc(repo / 'r.docx')
        doc.xml = doc.xml.replace('http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'http://purl.oclc.org/ooxml/wordprocessingml/main')
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertTrue(any('Strict' in p for p in res['problems']))

    def test_a_copied_row_never_replaces_the_original(self):
        repo = self.fresh('copied')
        u = self.key('Holds the cost and margin data')
        doc = Doc(repo / 'r.docx')
        row = doc.row(u.key)
        copy = row.replace('Holds the cost and margin data and makes the call on large discounts.', 'Approves large discounts.')
        copy = copy.replace('<w:tr>', '<w:tr><w:trPr><w:ins w:id="77" w:author="W" w:date="2026-10-09T00:00:00Z"/></w:trPr>', 1)
        start, end = doc.row_span(u.key)
        doc.xml = doc.xml[:end] + copy + doc.xml[end:]
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['changes'], [])
        self.assertTrue(any('copying' in s.reason for s in res['skipped']))
        self.assertIn(u.text, self.texts(repo))

    def test_a_row_without_its_id_is_reported(self):
        repo = self.fresh('noid')
        u = self.key('How the workshop worked')
        doc = Doc(repo / 'r.docx')
        doc.set_text(u.key, 'How the workshop ran')
        doc.xml = doc.xml.replace('ID %s' % u.key, 'I-D removed')
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertTrue(any('no ID' in s.reason for s in res['skipped']))

    def test_an_added_column_becomes_a_note(self):
        repo = self.fresh('column')
        u = self.key('Holds the cost and margin data')
        doc = Doc(repo / 'r.docx')
        start, end = doc.row_span(u.key)
        row = doc.xml[start:end].replace('</w:tr>', '<w:tc>%s</w:tc></w:tr>' % para(run('my notes')))
        doc.xml = doc.xml[:start] + row + doc.xml[end:]
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['changes'], [])
        self.assertEqual(res['skipped'], [])
        self.assertIn('my notes', res['report'])

    def test_merged_cells_are_refused(self):
        repo = self.fresh('merged')
        u = self.key('Holds the cost and margin data')
        doc = Doc(repo / 'r.docx')
        doc.set_text(u.key, 'Makes the call on large discounts.')
        start, end = doc.row_span(u.key)
        row = doc.xml[start:end]
        second = [x.start() for x in re.finditer('<w:tc>', row)][1]
        row = row[:second] + row[second:].replace('</w:tcPr>', '<w:vMerge w:val="restart"/></w:tcPr>', 1)
        doc.xml = doc.xml[:start] + row + doc.xml[end:]
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['changes'], [])
        self.assertTrue(any('changed shape' in s.reason for s in res['skipped']))

    def test_a_tracked_row_deletion_is_reported(self):
        repo = self.fresh('deleted')
        u = self.key('Holds the cost and margin data')
        doc = Doc(repo / 'r.docx')
        start, end = doc.row_span(u.key)
        row = doc.xml[start:end]
        row = row.replace('<w:tr>', '<w:tr><w:trPr><w:del w:id="91" w:author="W" w:date="2026-10-09T00:00:00Z"/></w:trPr>', 1)
        row = re.sub(r'<w:r>(<w:rPr>.*?</w:rPr>)?<w:t xml:space="preserve">(.*?)</w:t></w:r>',
                     lambda m: '<w:del w:id="92" w:author="W" w:date="2026-10-09T00:00:00Z"><w:r>%s<w:delText xml:space="preserve">%s</w:delText></w:r></w:del>' % (m.group(1) or '', m.group(2)), row)
        doc.xml = doc.xml[:start] + row + doc.xml[end:]
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['changes'], [])
        self.assertTrue(any('deleted this row' in s.reason for s in res['skipped']))

    def test_a_note_that_mentions_an_id_does_not_stop_the_import(self):
        repo = self.fresh('note')
        u = self.key('Jump to the recommendation')
        doc = Doc(repo / 'r.docx')
        doc.set_text(u.key, 'Go to the recommendation')
        doc.xml = doc.xml.replace('<w:body>', '<w:body>%s' % para(run('Note: the row ID %s reads oddly.' % u.key)), 1)
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['problems'], [])
        self.assertEqual(len(res['changes']), 1)

    def test_an_untracked_copy_reports_every_version_and_comment(self):
        repo = self.fresh('copy2')
        u = self.key('Holds the cost and margin data')
        doc = Doc(repo / 'r.docx')
        start, end = doc.row_span(u.key)
        copy = doc.row(u.key).replace('Holds the cost and margin data and makes the call on large discounts.', 'Approves large discounts.')
        copy = copy.replace('Approves large discounts.</w:t></w:r>', 'Approves large discounts.</w:t></w:r><w:r><w:commentReference w:id="5"/></w:r>')
        doc.xml = doc.xml[:end] + copy + doc.xml[end:]
        doc.parts['word/comments.xml'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:comments %s><w:comment w:id="5" w:author="W">'
                                          '<w:p><w:r><w:t>Add this as a new line?</w:t></w:r></w:p></w:comment></w:comments>' % W).encode()
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['changes'], [])
        entry = next(s for s in res['skipped'] if s.key == u.key)
        self.assertIn('Approves large discounts.', entry.reason)
        self.assertEqual([c['text'] for c in entry.comments], ['Add this as a new line?'])

    def test_rows_changed_on_the_page_after_the_export(self):
        repo = self.fresh('stale')
        a, b = self.key('Jump to the recommendation'), self.key('Holds the cost and margin data')
        doc = Doc(repo / 'r.docx')
        doc.set_text(b.key, 'Makes the call on large discounts.')   # the writer edits b; a is left alone
        doc.save()
        page = (repo / 'index.html').read_text(encoding='utf-8')     # the author changes both on the page
        page = page.replace('>Jump to the recommendation<', '>Go to the recommendation<')
        page = page.replace('Holds the cost and margin data and makes', 'Keeps the cost and margin data and makes')
        (repo / 'index.html').write_text(page, encoding='utf-8')
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['changes'], [])
        self.assertEqual(len(res['skipped']), 1)
        self.assertIn('merge them by hand', res['skipped'][0].reason)
        self.assertEqual(len(res['untouched_stale']), 1)

    def test_words_ending_in_a_colon_are_editable(self):
        self.fresh('colon')
        self.assertFalse(self.key('Boxes:').locked)

    def test_uncommitted_changes_are_flagged(self):
        import subprocess
        repo = self.fresh('dirty')
        if not shutil.which('git'):
            self.skipTest('git is not installed')
        for cmd in (['git', 'init', '-q'], ['git', 'add', '-A'], ['git', '-c', 'user.name=t', '-c', 'user.email=t@t',
                                                                    'commit', '-q', '-m', 'base']):
            subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)
        page = (repo / 'index.html').read_text(encoding='utf-8')
        (repo / 'index.html').write_text(page.replace('</body>', '<!-- my work -->\n</body>'), encoding='utf-8')
        u = self.key('Jump to the recommendation')
        doc = Doc(repo / 'r.docx')
        doc.set_text(u.key, 'Go to the recommendation')
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['dirty'], ['index.html'])
        self.assertIn('already had uncommitted changes', res['report'])


    def test_repeated_placeholder_is_refused_and_reordering_is_fine(self):
        repo = self.fresh('values')
        stage = self.key('Stage {1} of {2}')
        showing = self.key('Showing {1} of {2} changes')
        doc = Doc(repo / 'r.docx')
        removed = self.key('{1} changes recorded')
        doc.set_text(stage.key, '{3}: stage {1} of {2}')            # moved: fine
        doc.set_text(showing.key, 'Showing {1} of {2} changes ({1})')   # repeated: refused
        doc.set_text(removed.key, '{1} changes recorded for M1\u2013M2')  # {2} removed: refused
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['problems'], [])
        self.assertEqual([c.unit.key for c in res['changes']], [stage.key])
        reasons = {s.key: s.reason for s in res['skipped']}
        self.assertIn('placeholder', reasons[showing.key])
        self.assertIn('changed or removed', reasons[removed.key])
        page = (repo / 'index.html').read_text(encoding='utf-8')
        self.assertIn('`${stage.code}: stage ${stageIndex + 1} of ${STAGES.length}`', page)


class Links(Base):
    def test_links_follow_their_addresses(self):
        repo = self.fresh('links')
        aside = self.key('Also at the workshop:')
        doc = Doc(repo / 'r.docx')
        cell = para(run('Also at the workshop:', '<w:b/>'), run(' I showed '),
                    doc.link('https://github.com/nelmiux/AetherIaI', 'AetherIaI'),
                    run(' first. One of the Google presenters spoke well of TypeSafe AI’s Jev ('),
                    doc.link('https://docs.typesafe.ai/', 'their description'), run('); we have not evaluated it. '),
                    doc.link('https://example.org/new', 'A new link'), run('.'))
        doc.set_right(aside.key, cell)
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['problems'], [])
        self.assertEqual(len(res['changes']), 1, [s.reason for s in res['skipped']])
        page = (repo / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<a href="https://github.com/nelmiux/AetherIaI" rel="noopener" target="_blank"><span class="pname">AetherIaI</span></a> first.', page)
        self.assertIn('<a href="https://docs.typesafe.ai/" rel="noopener" target="_blank">their description</a>', page)
        self.assertIn('<a href="https://example.org/new" rel="noopener" target="_blank">A new link</a>', page)

    def test_removing_the_first_of_two_links(self):
        repo = self.fresh('unlink')
        aside = self.key('Also at the workshop:')
        doc = Doc(repo / 'r.docx')
        text = aside.text
        before, after = text.split('TypeSafe describes Jev', 1)
        before2, after2 = after.split('AetherIaI', 1)
        doc.set_right(aside.key, para(run('Also at the workshop:', '<w:b/>'), run(before[len('Also at the workshop:'):]),
                                      run('TypeSafe describes Jev'), run(before2),
                                      doc.link('https://github.com/nelmiux/AetherIaI', 'AetherIaI'), run(after2)))
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['problems'], [])
        self.assertEqual(len(res['changes']), 1, [s.reason for s in res['skipped']])
        page = (repo / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('href="https://docs.typesafe.ai/"', page)

    def test_a_phrase_link_cannot_be_broken(self):
        repo = self.fresh('magic')
        cap = self.key('Access control could not stop the override code')
        doc = Doc(repo / 'r.docx')
        doc.set_text(cap.key, cap.text.replace('(see Limits)', '(described under Limits)'))
        doc.save()
        res = run_import(repo, [str(repo / 'r.docx')])
        self.assertEqual(res['changes'], [])
        self.assertTrue(any('exact words' in s.reason for s in res['skipped']))

    def test_a_web_address_typed_into_markdown(self):
        repo = self.fresh('url')
        md = self.key('Both local servers listen on localhost only.')
        d = Doc(repo / 'd.docx')
        d.set_right(md.key, para(run(md.text + ' See https://example.org/a_b_c and '),
                                 d.link('https://example.org/x', 'https://example.org/x'), run('.')))
        d.save()
        res = run_import(repo, [str(repo / 'd.docx')])
        self.assertEqual(res['problems'], [])
        self.assertEqual(len(res['changes']), 1, [s.reason for s in res['skipped']])
        readme = (repo / 'README.md').read_text(encoding='utf-8')
        self.assertIn('See https://example.org/a_b_c and [https://example.org/x](https://example.org/x).', readme)

    def test_symbols_and_spaces_from_word(self):
        repo = self.fresh('symbols')
        pos = self.key('Positions are schematic.')
        md = self.key('Both local servers listen on localhost only.')
        doc = Doc(repo / 'r.docx')
        doc.set_right(pos.key, para(run('Positions are schematic '), '<w:r><w:sym w:font="Wingdings" w:char="F0E0"/></w:r>', run(' not to scale.')))
        doc.save()
        d = Doc(repo / 'd.docx')
        rest = md.text[len('Both local servers listen on localhost only.'):]
        d.set_right(md.key, para(run('Both local servers listen on '), run('localhost ', '<w:b/>'), run('only.' + rest)))
        d.save()
        res = run_import(repo, [str(repo / 'r.docx'), str(repo / 'd.docx')])
        self.assertEqual(res['problems'], [])
        self.assertEqual(len(res['changes']), 2, [s.reason for s in res['skipped']])
        texts = self.texts(repo)
        self.assertIn('Positions are schematic → not to scale.', texts)
        self.assertIn('**localhost** only.', (repo / 'README.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
