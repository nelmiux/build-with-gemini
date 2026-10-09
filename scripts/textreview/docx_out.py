"""Write the review document: a .docx with one table row per piece of text. Built with the standard
library only, so nothing needs installing."""
from __future__ import annotations

import datetime as _dt
import zipfile
from xml.sax.saxutils import escape as _x

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
LEFT_W, RIGHT_W = 2700, 6660          # twentieths of a point; Letter paper with 1-inch margins
GRAY, LIGHT, FAINT, LOCK_FILL, LINK = '4B5563', '6B7280', '9CA3AF', 'E5E7EB', '1D4ED8'
ID_PREFIX = 'ID '


def x(s):
    return _x(s, {'"': '&quot;'})


class Doc:
    def __init__(self):
        self.body = []
        self.rels = []        # (rid, target) for hyperlinks

    def link_rid(self, target):
        for rid, t in self.rels:
            if t == target:
                return rid
        rid = 'rIdL%d' % (len(self.rels) + 1)
        self.rels.append((rid, target))
        return rid

    # -- building blocks
    @staticmethod
    def run(text, bold=False, italic=False, mono=False, color=None, size=None, shade=None, underline=False):
        props = []
        if mono:
            props.append('<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/>')
        if bold:
            props.append('<w:b/><w:bCs/>')
        if italic:
            props.append('<w:i/><w:iCs/>')
        if color:
            props.append('<w:color w:val="%s"/>' % color)
        if size:
            props.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size, size))
        if underline:
            props.append('<w:u w:val="single"/>')
        if shade:
            props.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % shade)
        rpr = '<w:rPr>%s</w:rPr>' % ''.join(props) if props else ''
        return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, x(text))

    @staticmethod
    def para(runs, style=None, after=None, before=None, keep_next=False, indent=None):
        ppr = []
        if style:
            ppr.append('<w:pStyle w:val="%s"/>' % style)
        if keep_next:
            ppr.append('<w:keepNext/>')
        if after is not None or before is not None:
            ppr.append('<w:spacing%s%s/>' % (' w:before="%d"' % before if before is not None else '',
                                             ' w:after="%d"' % after if after is not None else ''))
        if indent:
            ppr.append('<w:ind w:left="%d" w:hanging="%d"/>' % indent)
        return '<w:p>%s%s</w:p>' % ('<w:pPr>%s</w:pPr>' % ''.join(ppr) if ppr else '', ''.join(runs))

    def add(self, xml):
        self.body.append(xml)

    def heading(self, text, level=1):
        self.add(self.para([self.run(text)], style='Heading%d' % level, keep_next=True))

    def text(self, text, **kw):
        self.add(self.para([self.run(text, **{k: v for k, v in kw.items() if k in ('bold', 'italic', 'color', 'size')})],
                           after=kw.get('after', 120)))

    def bullet(self, runs):
        self.add(self.para([self.run('•\t')] + runs, after=80, indent=(360, 360)))

    def table(self, rows, header=('Where it appears', 'Text: edit here')):
        border = '<w:%s w:val="single" w:sz="4" w:space="0" w:color="D1D5DB"/>'
        out = ['<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/><w:tblBorders>%s</w:tblBorders>'
               '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
               '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar>'
               '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>'
               % (LEFT_W + RIGHT_W, ''.join(border % b for b in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))),
               '<w:tblGrid><w:gridCol w:w="%d"/><w:gridCol w:w="%d"/></w:tblGrid>' % (LEFT_W, RIGHT_W)]
        cell = '<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>%s</w:tc>'
        shade = '<w:shd w:val="clear" w:color="auto" w:fill="F3F4F6"/>'
        out.append('<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>%s%s</w:tr>' % (
            cell % (LEFT_W, shade, self.para([self.run(header[0], bold=True, size=18)], after=0)),
            cell % (RIGHT_W, shade, self.para([self.run(header[1], bold=True, size=18)], after=0))))
        for left, right, band in rows:
            if band:   # a full-width sub-heading row
                out.append('<w:tr><w:trPr><w:cantSplit/></w:trPr><w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/><w:gridSpan w:val="2"/>%s</w:tcPr>%s</w:tc></w:tr>' % (
                    LEFT_W + RIGHT_W, '<w:shd w:val="clear" w:color="auto" w:fill="EEF2FF"/>',
                    self.para([self.run(band, bold=True, size=20, color='1E3A8A')], after=0, keep_next=True)))
                continue
            out.append('<w:tr>%s%s</w:tr>' % (cell % (LEFT_W, '', ''.join(left)), cell % (RIGHT_W, '', ''.join(right))))
        out.append('</w:tbl>')
        self.add(''.join(out))
        self.add(self.para([], after=0))

    # -- the package
    def save(self, path, title, author):
        now = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        sect = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" '
                'w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
        document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<w:document xmlns:w="%s" xmlns:r="%s"><w:body>%s%s</w:body></w:document>' % (W_NS, R_NS, ''.join(self.body), sect))
        rels = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
                '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>']
        rels += ['<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                 'Target="%s" TargetMode="External"/>' % (rid, x(t)) for rid, t in self.rels]
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', CONTENT_TYPES)
            z.writestr('_rels/.rels', ROOT_RELS)
            z.writestr('docProps/core.xml', CORE % (x(title), x(author), now, now))
            z.writestr('docProps/app.xml', APP)
            z.writestr('word/document.xml', document)
            z.writestr('word/styles.xml', STYLES)
            z.writestr('word/settings.xml', SETTINGS)
            z.writestr('word/_rels/document.xml.rels',
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % ''.join(rels))


CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>'''

ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>'''

CORE = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>%s</dc:title><dc:creator>%s</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>'''

APP = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>build-with-gemini text review</Application></Properties>'''

SETTINGS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:trackRevisions/><w:defaultTabStop w:val="360"/><w:characterSpacingControl w:val="doNotCompress"/><w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>'''

STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" w:eastAsia="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/><w:szCs w:val="40"/><w:color w:val="111827"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:szCs w:val="30"/><w:color w:val="111827"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/><w:color w:val="1F2937"/></w:rPr></w:style>
<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="1D4ED8"/><w:u w:val="single"/></w:rPr></w:style>
<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>'''
