"""Markdown math tokens: preserve TeX before Markdown escaping/emphasis."""
from html import escape


def escaped(s, pos):
    n = 0
    while pos > 0 and s[pos - 1] == '\\':
        n += 1
        pos -= 1
    return n % 2 == 1


def closing(s, delimiter, start):
    pos = s.find(delimiter, start)
    while pos != -1 and escaped(s, pos):
        pos = s.find(delimiter, pos + len(delimiter))
    return pos


def math_block(state, start, end, silent):
    if state.sCount[start] - state.blkIndent >= 4:
        return False
    first = state.src[state.bMarks[start] + state.tShift[start]:state.eMarks[start]]
    if first.startswith('$$'):
        op = cl = '$$'
    elif first.startswith(r'\['):
        op, cl = r'\[', r'\]'
    else:
        return False
    rest = first[len(op):]
    pos = closing(rest, cl, 0)
    if pos >= 0:
        if rest[pos + len(cl):].strip() or not rest[:pos].strip():
            return False
        tex, stop = rest[:pos], start + 1
    else:
        lines = [rest]
        stop = start + 1
        while stop < end:
            if not state.isEmpty(stop) and state.sCount[stop] < state.blkIndent:
                return False
            line = state.src[state.bMarks[stop] + state.tShift[stop]:state.eMarks[stop]]
            pos = closing(line, cl, 0)
            if pos >= 0:
                if line[pos + len(cl):].strip():
                    return False
                lines.append(line[:pos]); stop += 1
                break
            lines.append(line); stop += 1
        else:
            return False
        tex = '\n'.join(lines)
        if not tex.strip():
            return False
    if silent:
        return True
    token = state.push('math_block', 'div', 0)
    token.block = True; token.content = tex.strip(); token.map = [start, stop]
    state.line = stop
    return True


def math_inline(state, silent):
    s, p, end = state.src, state.pos, state.posMax
    if s.startswith(r'\(', p):
        op, cl = r'\(', r'\)'
    elif s.startswith(r'\[', p):
        op, cl = r'\[', r'\]'
    elif s.startswith('$$', p):
        op = cl = '$$'
    elif s[p:p+1] == '$':
        op = cl = '$'
    else:
        return False
    begin = p + len(op)
    if begin >= end or (op == '$' and (s[begin].isspace() or s[begin] == '$')):
        return False
    q = closing(s[:end], cl, begin)
    if q < 0 or not s[begin:q].strip():
        return False
    if '\n' in s[begin:q]:
        return False
    if op == '$' and (s[q-1].isspace() or (q+1 < end and (s[q+1].isdigit() or s[q+1]=='$'))):
        return False
    if not silent:
        token = state.push('math_inline', 'span', 0)
        token.content = s[begin:q].strip()
        token.meta = {'display': op in ('$$', r'\[')}
    state.pos = q + len(cl)
    return True


def render_math(tokens, idx, options, env):
    t = tokens[idx]
    display = t.type == 'math_block' or t.meta.get('display', False)
    env['math_count'] = env.get('math_count', 0) + 1
    tag = 'div' if t.type == 'math_block' else 'span'
    return f'<{tag} class="math-tex math-{ "display" if display else "inline" }" data-display="{str(display).lower()}">{escape(t.content)}</{tag}>' + ('\n' if t.type == 'math_block' else '')


def math_plugin(md):
    md.block.ruler.before('fence', 'math_block', math_block, {'alt': ['paragraph', 'reference', 'blockquote', 'list']})
    md.inline.ruler.before('escape', 'math_inline', math_inline)
    md.renderer.rules['math_block'] = render_math
    md.renderer.rules['math_inline'] = render_math
