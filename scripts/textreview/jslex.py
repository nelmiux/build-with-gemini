"""A small JavaScript lexer: enough to find every string and template literal in the page scripts with
exact source positions, and to tell which data structure each one sits in."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

IDENT_RE = re.compile(r'[A-Za-z_$][\w$]*')
NUM_RE = re.compile(r'0[xXbBoO][0-9a-fA-F_]+n?|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?n?')
# A '/' starts a regular expression after these (otherwise it divides).
REGEX_AFTER_PUNCT = set('(,=:[!&|?{};+-*%<>~^')
REGEX_AFTER_WORD = {'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void', 'throw', 'case', 'do',
                    'else', 'yield', 'await'}
SIMPLE_ESC = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', 'v': '\v'}


class JSLexError(Exception):
    pass


@dataclass
class Tok:
    kind: str                 # ident | num | str | tpl | regex | punct
    start: int
    end: int
    value: str = ''           # identifier, punctuation, or the decoded value of a string
    quote: str = ''           # for str: the quote character
    parts: list = field(default_factory=list)   # for tpl: ('lit', start, end) and ('expr', start, end, [Tok])
    path: tuple = ()          # set by annotate(): (('const', name), (kind, key_or_index, callee, frame_id), ...)
    is_key: bool = False      # a string used as an object key
    concat: bool = False      # a string joined to other values with +


def js_unescape(raw):
    """Decode a string or template body. Returns (text, starts, ends): for each decoded character, the span
    of source characters it came from (relative to raw)."""
    out, starts, ends = [], [], []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch == '\\' and i + 1 < n:
            nx = raw[i + 1]
            if nx in SIMPLE_ESC:
                out.append(SIMPLE_ESC[nx]); starts.append(i); ends.append(i + 2); i += 2; continue
            if nx == '0' and not (i + 2 < n and raw[i + 2].isdigit()):
                out.append('\0'); starts.append(i); ends.append(i + 2); i += 2; continue
            if nx == 'x':
                out.append(chr(int(raw[i + 2:i + 4], 16))); starts.append(i); ends.append(i + 4); i += 4; continue
            if nx == 'u':
                if raw[i + 2:i + 3] == '{':
                    j = raw.index('}', i)
                    out.append(chr(int(raw[i + 3:j], 16))); starts.append(i); ends.append(j + 1); i = j + 1; continue
                out.append(chr(int(raw[i + 2:i + 6], 16))); starts.append(i); ends.append(i + 6); i += 6; continue
            if nx == '\r' and raw[i + 2:i + 3] == '\n':
                i += 3; continue
            if nx in '\n\r\u2028\u2029':
                i += 2; continue
            out.append(nx); starts.append(i); ends.append(i + 2); i += 2; continue
        out.append(ch); starts.append(i); ends.append(i + 1); i += 1
    return ''.join(out), starts, ends


def js_escape(text, quote):
    """Encode text for the inside of a string or template literal with the given quote."""
    out = text.replace('\\', '\\\\')
    if quote == '`':
        out = out.replace('`', '\\`').replace('${', '\\${')
    else:
        out = out.replace(quote, '\\' + quote).replace('\n', '\\n').replace('\r', '\\r')
    out = out.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    # inside an HTML page, "</script" or "<!--" in a string would end or confuse the script element
    out = re.sub(r'</(script)', r'<\\/\1', out, flags=re.I)
    return out.replace('<!--', '<\\!--')


def _regex_ok(toks):
    if not toks:
        return True
    t = toks[-1]
    if t.kind == 'punct':
        return t.value in REGEX_AFTER_PUNCT
    if t.kind == 'ident':
        return t.value in REGEX_AFTER_WORD
    return False


def _skip_regex(src, pos, n):
    j, in_class = pos + 1, False
    while j < n:
        ch = src[j]
        if ch == '\\':
            j += 2; continue
        if ch == '\n':
            raise JSLexError(f'line break inside a regular expression at {pos}')
        if in_class:
            if ch == ']':
                in_class = False
        elif ch == '[':
            in_class = True
        elif ch == '/':
            break
        j += 1
    j += 1
    while j < n and (src[j].isalnum() or src[j] == '_'):
        j += 1
    return j


def lex(src, pos=0, end=None, in_expr=False):
    """Tokenize src[pos:end]. With in_expr, stop at the '}' that closes a template ${...} and return its index."""
    n = len(src) if end is None else end
    toks, depth = [], 0
    while pos < n:
        c = src[pos]
        if c in ' \t\r\n\f\v\u00a0\ufeff\u2028\u2029':
            pos += 1; continue
        if src.startswith('//', pos):
            j = src.find('\n', pos); pos = n if j < 0 else j; continue
        if src.startswith('/*', pos):
            j = src.find('*/', pos + 2)
            if j < 0:
                raise JSLexError(f'unterminated comment at {pos}')
            pos = j + 2; continue
        if c in '\'"':
            j = pos + 1
            while True:
                if j >= n:
                    raise JSLexError(f'unterminated string at {pos}')
                ch = src[j]
                if ch == '\\':
                    j += 2; continue
                if ch == c:
                    break
                if ch == '\n':
                    raise JSLexError(f'line break inside a string at {pos}')
                j += 1
            toks.append(Tok('str', pos, j + 1, js_unescape(src[pos + 1:j])[0], quote=c))
            pos = j + 1; continue
        if c == '`':
            tok, pos = _lex_template(src, pos, n)
            toks.append(tok); continue
        if c == '/' and _regex_ok(toks):
            j = _skip_regex(src, pos, n)
            toks.append(Tok('regex', pos, j, src[pos:j])); pos = j; continue
        m = IDENT_RE.match(src, pos)
        if m:
            toks.append(Tok('ident', pos, m.end(), m.group())); pos = m.end(); continue
        if c.isdigit() or (c == '.' and pos + 1 < n and src[pos + 1].isdigit()):
            m = NUM_RE.match(src, pos)
            toks.append(Tok('num', pos, m.end(), m.group())); pos = m.end(); continue
        if in_expr:
            if c == '{':
                depth += 1
            elif c == '}':
                if depth == 0:
                    return toks, pos
                depth -= 1
        toks.append(Tok('punct', pos, pos + 1, c)); pos += 1
    if in_expr:
        raise JSLexError('unterminated ${ expression')
    return toks, pos


def _lex_template(src, pos, n):
    start, pos, parts = pos, pos + 1, []
    lit_start = pos
    while True:
        if pos >= n:
            raise JSLexError(f'unterminated template literal at {start}')
        ch = src[pos]
        if ch == '\\':
            pos += 2; continue
        if ch == '`':
            parts.append(('lit', lit_start, pos))
            return Tok('tpl', start, pos + 1, parts=parts), pos + 1
        if ch == '$' and src.startswith('${', pos):
            parts.append(('lit', lit_start, pos))
            sub, close = lex(src, pos + 2, n, in_expr=True)
            parts.append(('expr', pos, close + 1, sub))
            pos = close + 1
            lit_start = pos
            continue
        pos += 1


def _object_brace(prev):
    """Whether a '{' after this token opens an object literal (rather than a block)."""
    if prev is None:
        return False
    if prev.kind == 'punct':
        return prev.value in '(,=:[?!&|+'
    if prev.kind == 'ident':
        return prev.value in ('return', 'typeof', 'in', 'of', 'yield', 'await')
    return False


def annotate(toks, prefix=(('const', None),)):
    """Record on every token the chain of containers it sits in, starting from the declaration it belongs to:
    (('const', 'CAST'), ('[', 2, None, id), ('{', 'what', None, id)).
    Frames are [ (array, with element index), { (object, with current key), ( (call, with callee and argument
    index), or 'block'. Template expressions are annotated recursively."""
    stack = []
    const = prefix[0][1] if prefix and prefix[0][0] == 'const' else None
    top_level = prefix == (('const', None),)
    frame_id = [0]

    def new_id():
        frame_id[0] += 1
        return frame_id[0]

    for i, t in enumerate(toks):
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if top_level and not stack and t.kind == 'ident' and t.value in ('const', 'let', 'var', 'function', 'class'):
            if nxt is not None and nxt.kind == 'ident':
                const = nxt.value
        head = (('const', const),) + tuple(prefix[1:])
        if t.kind == 'punct':
            v = t.value
            if v in '([{':
                kind = v if v != '{' else ('{' if _object_brace(prev) else 'block')
                callee = prev.value if (v == '(' and prev is not None and prev.kind == 'ident') else None
                if v in '[{' and prev is not None and prev.kind == 'punct' and prev.value == '=' and i >= 2 and toks[i - 2].kind == 'ident':
                    callee = toks[i - 2].value   # the name an object or array literal is assigned to
                t.path = head + tuple((f[0], f[1], f[2], f[4]) for f in stack)
                stack.append([kind, 0 if v in '([' else None, callee, 0, new_id()])
                continue
            if v in ')]}':
                if stack:
                    closed = stack.pop()
                    if top_level and not stack and closed[0] == 'block':
                        const = None
                t.path = head + tuple((f[0], f[1], f[2], f[4]) for f in stack)
                continue
            if v == ';' and top_level and not stack:
                const = None
            elif v == ',' and stack:
                f = stack[-1]
                if f[0] in ('[', '('):
                    f[1] += 1
                elif f[0] == '{':
                    f[1] = None
            elif v == '?' and stack and not (nxt is not None and nxt.kind == 'punct' and nxt.value == '.'):
                stack[-1][3] += 1
            elif v == ':' and stack and stack[-1][0] == '{':
                f = stack[-1]
                if f[3] > 0:
                    f[3] -= 1
                elif prev is not None and prev.kind in ('ident', 'str', 'num'):
                    f[1] = prev.value
        t.path = head + tuple((f[0], f[1], f[2], f[4]) for f in stack)
        if t.kind == 'str':
            top = stack[-1] if stack else None
            t.is_key = (top is not None and top[0] == '{' and top[3] == 0 and nxt is not None
                        and nxt.kind == 'punct' and nxt.value == ':')
            t.concat = ((prev is not None and prev.kind == 'punct' and prev.value == '+')
                        or (nxt is not None and nxt.kind == 'punct' and nxt.value == '+'))
        if t.kind == 'tpl':
            for part in t.parts:
                if part[0] == 'expr':
                    annotate(part[3], prefix=t.path + (('${', None, None, 0),))


def walk(toks):
    """Every token, including those inside template expressions, in source order."""
    for t in toks:
        yield t
        if t.kind == 'tpl':
            for part in t.parts:
                if part[0] == 'expr':
                    yield from walk(part[3])
