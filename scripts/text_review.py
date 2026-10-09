#!/usr/bin/env python3
"""Send the site's text to a writer as a Word document, and bring the edits back.

  npm run text:export                    write text-review/report-text.docx and text-review/docs-text.docx
  npm run text:import -- FILE.docx       write the edits in FILE.docx back into the pages
  npm run text:import -- FILE.docx --preview    show what would change, without changing anything
  npm run text:check                     check that export and import round-trip without changing anything

Needs Python 3.9 or newer and nothing else. See README.md, "Editing the text with a writer".
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from textreview.apply import run_import          # noqa: E402
from textreview.export import export             # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'text-review'


def reviewed(path):
    """Whether a document has been opened and edited since the tool wrote it (so it must not be overwritten)."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            app = z.read('docProps/app.xml').decode('utf-8', 'replace') if 'docProps/app.xml' in names else ''
            doc = z.read('word/document.xml').decode('utf-8', 'replace') if 'word/document.xml' in names else ''
    except (zipfile.BadZipFile, OSError):
        return True
    return ('build-with-gemini text review' not in app or 'word/comments.xml' in names
            or '<w:ins ' in doc or '<w:del ' in doc)


def free_name(path):
    n = 2
    while True:
        candidate = path.with_name('%s-%d%s' % (path.stem, n, path.suffix))
        if not candidate.exists():
            return candidate
        n += 1


def cmd_export(args):
    OUT.mkdir(exist_ok=True)
    which = ['report', 'docs'] if args.which == 'all' else [args.which]
    for w in which:
        path = OUT / ('report-text.docx' if w == 'report' else 'docs-text.docx')
        if path.exists() and reviewed(path):
            kept = path
            path = free_name(path)
            print('%s looks like a reviewed copy, so it was kept; the new export is %s.' % (kept.relative_to(ROOT), path.name))
        info = export(ROOT, path, w)
        print('Wrote %s: %s pieces of text, about %s words.' % (path.relative_to(ROOT), '{:,}'.format(info['pieces']), '{:,}'.format(info['words'])))
    print('\nSend the file to the writer. When it comes back, save it (any name) and run:\n'
          '  npm run text:import -- path/to/the-file.docx')
    return 0


SAVE_AS_DOCX = ('Save it as a Word document (.docx) and try again: in Word, File > Save As > Word Document; '
                'in Google Docs, File > Download > Microsoft Word (.docx); in Pages, File > Export To > Word.')


def resolve(path):
    """A path as the user typed it: npm runs scripts from the project folder, but INIT_CWD is where they were."""
    p = pathlib.Path(path).expanduser()
    if not p.is_absolute():
        p = pathlib.Path(os.environ.get('INIT_CWD') or os.getcwd()) / p
    return p


def check_docx(path):
    """A friendly reason why this file cannot be imported, or None."""
    import zipfile
    if not path.exists():
        return 'File not found: %s' % path
    if path.is_dir():
        return '%s is a folder (a .pages file saved as a package?). %s' % (path, SAVE_AS_DOCX)
    if not zipfile.is_zipfile(path):
        return '%s is not a .docx file. %s' % (path.name, SAVE_AS_DOCX)
    with zipfile.ZipFile(path) as z:
        if 'word/document.xml' not in z.namelist():
            return '%s is not a Word document (.docx). %s' % (path.name, SAVE_AS_DOCX)
    return None


def cmd_import(args):
    files = [resolve(f) for f in args.files]
    for f in files:
        reason = check_docx(f)
        if reason:
            print(reason)
            return 2
    res = run_import(ROOT, [str(f) for f in files], dry_run=args.preview, report_dir=OUT)
    changes, skipped, problems = res['changes'], res['skipped'], res['problems']
    if problems:
        print('Nothing was written: a check failed.')
        for p in problems:
            print('  ' + p)
    elif args.preview:
        print('Preview: %d edit%s would be written; nothing was changed.' % (len(changes), '' if len(changes) == 1 else 's'))
    else:
        places = sum(len(c.unit.occs) for c in changes)
        print('%d edit%s written to %d place%s%s.' % (len(changes), '' if len(changes) == 1 else 's', places,
              '' if places == 1 else 's', (' in ' + ', '.join(res['written'])) if res['written'] else ''))
    if skipped:
        print('%d row%s not applied (the report says why).' % (len(skipped), '' if len(skipped) == 1 else 's'))
    if res.get('dirty') and not problems:
        print('Note: %s already had uncommitted changes; they are now mixed with the writer\'s edits. Review with git diff.'
              % ', '.join(res['dirty']))
    for name, n in res.get('docs_read', []):
        print('  %s: %d row%s read' % (name, n, '' if n == 1 else 's'))
    if res['comments']:
        print('%d comment%s from the writer %s listed in the report.' % (res['comments'], '' if res['comments'] == 1 else 's',
                                                                       'is' if res['comments'] == 1 else 'are'))
    print('Report: %s' % os.path.relpath(res['report_path'], ROOT))
    if res['written']:
        print('\nNext: check the pages with "npm start", review with "git diff", then commit and push.')
    return 1 if problems else 0


def cmd_check(args):
    """Export, read the untouched documents back, and confirm that nothing would change."""
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for w in ('report', 'docs'):
            path = pathlib.Path(tmp) / ('%s.docx' % w)
            export(ROOT, path, w)
            paths.append(str(path))
        res = run_import(ROOT, paths, dry_run=True, report_dir=None)
        if res['changes'] or res['skipped'] or res['problems']:
            ok = False
            print('Round trip is NOT clean:')
            for c in res['changes'][:10]:
                print('  would change: %s' % c.unit.occs[0].where)
            for s in res['skipped'][:10]:
                print('  skipped: %s: %s' % (s.where, s.reason))
            for p in res['problems'][:10]:
                print('  problem: %s' % p)
    print('Round trip is clean: exporting and importing unchanged documents changes nothing.' if ok else '')
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description='Send the site text to a writer and bring the edits back.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('export', help='write the review document(s) into text-review/')
    e.add_argument('which', nargs='?', default='all', choices=['all', 'report', 'docs'])
    i = sub.add_parser('import', help='write the edits from reviewed document(s) back into the pages')
    i.add_argument('files', nargs='+')
    i.add_argument('--preview', action='store_true', help='show what would change without changing anything')
    sub.add_parser('check', help='confirm that an untouched export imports with no changes')
    args = ap.parse_args(argv)
    return {'export': cmd_export, 'import': cmd_import, 'check': cmd_check}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
