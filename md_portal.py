#!/usr/bin/env python3
"""Build a standalone, offline Markdown documentation portal."""
import argparse, base64, html, json, mimetypes, os, re, sys, unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from bs4 import BeautifulSoup, NavigableString
import yaml
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from portal_assets import Assets, compact_markup, minify_shell
from portal_compression import optimize, ALGORITHMS, ENCODINGS
from portal_math import math_plugin
from portal_render import prerender
from portal_nav import build_navigation, navigation_order

EXTS={'.md','.markdown','.mdown'}
IGNORE={'.git','.venv','venv','node_modules','__pycache__','.idea','bin','obj'}

def slug(s):
    s=unicodedata.normalize('NFKD',s.lower())
    s=''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^\w-]+','-',s).strip('-') or 'sezione'

# Only empty, attribute-limited anchors are recognized. General raw HTML stays disabled.
EXPLICIT_ANCHOR=re.compile(r"<a[ \t]+(?:id|name)[ \t]*=[ \t]*(?:\"([\w.:-]+)\"|'([\w.:-]+)')[ \t]*>[ \t]*</a[ \t]*>", re.I)

def explicit_anchor_plugin(md):
    def parse(state, silent):
        match=EXPLICIT_ANCHOR.match(state.src,state.pos,state.posMax)
        if not match: return False
        if not silent:
            token=state.push('html_inline','',0)
            token.content='<span data-md-anchor="'+html.escape(match[1] or match[2],quote=True)+'"></span>'
        state.pos=match.end()
        return True
    md.inline.ruler.before('text','explicit_anchor',parse)

def register_explicit_anchors(soup, aliases, used, warn):
    seen=set()
    # A standalone anchor preceding a heading is a separate scroll target, not a TOC entry.
    for tag in soup.select('span[data-md-anchor]'):
        key=tag['data-md-anchor']; del tag['data-md-anchor']
        if key in seen:
            warn(f'Duplicate explicit anchor: #{key}'); tag.decompose(); continue
        seen.add(key)
        target=key; i=1
        while target in used or target=='allegati-incorporati':
            target=f'anchor-{key}-{i}'; i+=1
        used.add(target); tag['id']='section-'+target; tag['class']=['md-explicit-anchor']
        aliases[key]=target
        # Remove the empty paragraph wrapper generated for standalone anchors.
        parent=tag.parent
        if parent.name=='p' and all(c is tag or isinstance(c,NavigableString) and not c.strip() for c in parent.contents):
            tag.extract(); parent.replace_with(tag)
    return len(seen)

def route(path, anchor=''):
    return '#/'+quote(path,safe='/')+('#'+quote(anchor,safe='') if anchor else '')

def order_key(p):
    return (p.name.lower() not in ('readme.md','index.md'),p.as_posix().casefold())

def js_json(obj, compact=False):
    return json.dumps(obj,ensure_ascii=False,separators=(',',':') if compact else None).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')

STATUS_ALIASES={
    'da fare':'da fare','dafare':'da fare','todo':'da fare','planned':'da fare',
    'in corso':'in corso','incorso':'in corso','in progress':'in corso','doing':'in corso',
    'fatta':'fatta','fatto':'fatta','done':'fatta','implementato':'fatta','completed':'fatta',
    'bloccata':'bloccata','bloccato':'bloccata','blocked':'bloccata',
    'abbandonata':'abbandonata','abbandonato':'abbandonata','cancelled':'abbandonata','canceled':'abbandonata',
}
STATUS_ORDER=['da fare','in corso','fatta','bloccata','abbandonata','non specificato']
FEATURE_ID=re.compile(r'(?<![\w-])(?:[A-Z][A-Z0-9]{1,15}-[0-9]+|F[0-9]+)(?![\w-])',re.I)

def text_value(value):
    return str(value).strip() if isinstance(value,(str,int,float)) and not isinstance(value,bool) else ''

def list_value(value):
    if isinstance(value,list): return [text_value(v) for v in value if text_value(v)]
    if isinstance(value,str): return [v.strip() for v in re.split(r'[,;]',value) if v.strip()]
    return []

def read_metadata(source, path, warn):
    meta={}
    if re.match(r'^---[ \t]*\r?\n',source):
        m=re.match(r'^---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|$)',source,re.S|re.M)
        if m:
            try:
                data=yaml.safe_load(m[1]);
                if isinstance(data,dict): meta={str(k).lower():v for k,v in data.items()}; source=source[m.end():]
                elif data is not None: warn('Front matter YAML must be a mapping')
            except yaml.YAMLError as exc: warn('Invalid YAML front matter: '+str(exc).splitlines()[0])
    def pick(*keys):
        return next((meta[k] for k in keys if k in meta),None)
    result={k:text_value(pick(*aliases)) for k,aliases in {
        'title':('title','titolo'),'id':('id','feature_id'),'type':('type','tipo'),
        'service':('service','servizio','module','modulo'),'status':('status','stato'),
        'version':('version','versione'),'author':('author','autore'),'owner':('owner','responsabile')
    }.items()}
    result['tags']=list_value(pick('tags','etichette'))
    result['depends_on']=list_value(pick('depends_on','dependencies','dipende_da'))
    result['enables']=list_value(pick('enables','abilita'))
    parts=Path(path).parts
    in_features=any(x.lower() in ('features','funzionalità','funzionalita') for x in parts[:-1])
    candidate=re.match(r'((?:[A-Z][A-Z0-9]{1,15}-[0-9]+|F[0-9]+))(?=[_ .-]|$)',Path(path).stem,re.I)
    feature=result['type'].lower() in ('feature','funzionalità','funzionalita') or bool(result['id']) or bool(in_features and candidate)
    if feature:
        result['type']='feature'
        if not result['id'] and candidate: result['id']=candidate[1].upper()
        if not result['service']:
            if len(parts)>2 and parts[0]=='apps': result['service']=parts[1]
            elif in_features:
                ix=next(i for i,x in enumerate(parts) if x.lower() in ('features','funzionalità','funzionalita'))
                pre=[x for x in parts[:ix] if x.lower() not in ('doc','docs')]; result['service']=pre[-1] if pre else 'Generale'
            else: result['service']='Generale'
        if not result['status']:
            m=re.search(r'^\s*(?:[-*]\s+)?(?:\*\*Stato\*\*\s*:|\*\*Stato:\*\*|Stato:)\s*(.*?)\s*$',source,re.I|re.M)
            if m: result['status']=m[1].replace('*','').strip()
        raw=result['status']; result['status']=STATUS_ALIASES.get(raw.lower().replace('_',' ').replace('-',' '),raw or 'non specificato')
    return source,result,meta

def resolve_inline_properties(soup, properties, warn):
    """Resolve simple Dataview-style local YAML references, never evaluate code."""
    resolved=0
    for code in list(soup.find_all("code")):
        if code.find_parent("pre"):
            continue
        match=re.fullmatch(r"=\s*this\.([\w-]+)",code.get_text().strip())
        if not match:
            continue
        field=match[1].lower()
        value=properties.get(field)
        if field not in properties or value is None or not isinstance(value,(str,int,float,bool)):
            warn(f"Unresolved inline property: {match[0]} (missing or non-scalar YAML field)")
            continue
        text=str(value).lower() if isinstance(value,bool) else str(value)
        code.replace_with(NavigableString(text))
        resolved+=1
    return resolved

def legacy_dependencies(soup,meta):
    if meta['type']!='feature': return
    for table in soup.find_all('table'):
        for tr in table.find_all('tr'):
            cells=tr.find_all(['th','td'],recursive=False)
            if len(cells)<2: continue
            key=cells[0].get_text(' ',strip=True).lower()
            field={'dipende da':'depends_on','abilita':'enables'}.get(key)
            if field and not meta[field]:
                meta[field]=list(dict.fromkeys(x.upper() for x in FEATURE_ID.findall(' '.join(c.get_text(' ',strip=True) for c in cells[1:]))))
        headers=[c.get_text(' ',strip=True).lower() for c in table.find_all('th')]
        for label,field in [('dipende da','depends_on'),('abilita','enables')]:
            if label in headers and not meta[field]:
                i=headers.index(label); vals=[]
                for tr in table.find_all('tr'):
                    cells=tr.find_all('td',recursive=False)
                    if len(cells)>i: vals.extend(FEATURE_ID.findall(cells[i].get_text(' ',strip=True)))
                meta[field]=list(dict.fromkeys(v.upper() for v in vals))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('inputs',nargs='+',help='Markdown files and/or folders (recursive)')
    ap.add_argument('-o','--output',default='docs.html')
    ap.add_argument('--title',default='Documentazione')
    ap.add_argument('--root',type=Path,help='Base directory for navigation and allowed local assets')
    ap.add_argument('--home',help='Initial page, relative to root (e.g. README.md)')
    ap.add_argument('--strict',action='store_true',help='Abort on unresolved local links or images')
    ap.add_argument('--no-mermaid',action='store_true',help='Leave Mermaid blocks as source code')
    ap.add_argument('--render-mode',choices=['static','runtime'],default='static',help='SVG at build time (default) or render in recipient browser')
    ap.add_argument('--browser-path',type=Path,help='Explicit Chromium/Chrome/Edge executable (default: auto-detect local browser)')
    ap.add_argument('--no-math',action='store_true',help='Disable LaTeX formula rendering')
    ap.add_argument('--no-dashboard',action='store_true',help='Do not generate the feature dashboard')
    ap.add_argument('--no-implicit-assets',action='store_true',help='Embed only explicit Markdown links and images')
    ap.add_argument('--no-minify',action='store_true',help='Keep original HTML, CSS, JS and JSON formatting')
    ap.add_argument('--compression',choices=['auto','none',*ALGORITHMS],default='auto',help='Lossless self-extracting HTML (default: auto, only if smaller)')
    ap.add_argument('--profile',choices=['compatible','maximum'],default='maximum',help='Native gzip/deflate only, or embedded JS decoders (default)')
    ap.add_argument('--encoding',choices=['auto',*ENCODINGS],default='auto',help='Payload encoding (default: compare all)')
    ap.add_argument('--zopfli-iterations',type=int,default=5,help='Zopfli iteration count (default 5)')
    ap.add_argument('--zstd-decoder',type=Path,help='Optional local fzstd UMD browser decoder')
    ap.add_argument('--report',type=Path,help='Write a JSON size and resource manifest')
    args=ap.parse_args()
    inputs=[Path(x).expanduser().resolve() for x in args.inputs]
    for p in inputs:
        if not p.exists(): ap.error(f'Input not found: {p}')
        if p.is_file() and p.suffix.lower() not in EXTS: ap.error(f'Not a Markdown file: {p}')
    root=args.root.expanduser().resolve() if args.root else Path(os.path.commonpath([str(p if p.is_dir() else p.parent) for p in inputs]))
    files=set()
    for p in inputs:
        candidates=p.rglob('*') if p.is_dir() else [p]
        for f in candidates:
            if f.is_file() and f.suffix.lower() in EXTS and not any(x in IGNORE or x.startswith('.') for x in f.relative_to(p if p.is_dir() else p.parent).parts):
                f=f.resolve()
                if not f.is_relative_to(root): ap.error(f'File outside root: {f}')
                files.add(f)
    files=sorted(files,key=lambda f:order_key(f.relative_to(root)))
    if not files: ap.error('No Markdown documents found')
    paths={f:f.relative_to(root).as_posix() for f in files}
    md=MarkdownIt('commonmark',{'html':False}).enable(['table','strikethrough']).use(explicit_anchor_plugin)
    if not args.no_math: md.use(math_plugin)
    warnings=[]; pages=[]; soups={}; headings_by_file={}; diagram_count=0; math_count=0; property_count=0; anchor_count=0
    assets=Assets(root,IGNORE,EXTS,excluded=[Path(args.output).resolve()]+([args.report.resolve()] if args.report else []))
    attachments={}
    print(f'[1/4] Lettura Markdown: {len(files)} documenti; root: {root}')
    def warn(f,msg): warnings.append(f'{paths[f]}: {msg}')
    for f in files:
        source=f.read_text(encoding='utf-8-sig')
        source,meta,properties=read_metadata(source,paths[f],lambda msg:warn(f,msg))
        env={}
        soup=BeautifulSoup(md.render(source,env),'html.parser'); soups[f]=soup
        property_count+=resolve_inline_properties(soup,properties,lambda msg:warn(f,msg))
        math_count+=env.get('math_count',0)
        legacy_dependencies(soup,meta)
        used=set(); headings=[]; aliases={}
        for h in soup.find_all(re.compile(r'^h[1-6]$')):
            label=h.get_text(' ',strip=True); base=slug(label); key=base; i=1
            while key in used: key=f'{base}-{i}'; i+=1
            used.add(key); h['id']='section-'+key; headings.append({'id':key,'text':label,'level':int(h.name[1])})
            aliases[key]=key
            # GitHub-like Unicode headings, including punctuation removal.
            gh=re.sub(r'[^\w\- ]','',label.lower()).replace(' ','-')
            aliases.setdefault(gh,key)
        anchor_count+=register_explicit_anchors(soup,aliases,used,lambda msg:warn(f,msg))
        headings_by_file[f]=aliases
        title=next((h['text'] for h in headings if h['level']==1),f.stem.replace('_',' '))
        title=meta['title'] or title
        pages.append({'path':paths[f],'title':title,'headings':headings,'text':soup.get_text(' ',strip=True)+' '+ ' '.join(str(v) for v in meta.values()),'meta':meta})
    print('[2/4] Risoluzione collegamenti, immagini e allegati…')
    for f,soup in soups.items():
        attached=set(); attachments[f]=attached
        for img in soup.find_all('img',src=True):
            src=img['src']; u=urlsplit(src)
            if u.scheme or u.netloc:
                if u.scheme!='data': warn(f,f'External image requires network: {src}')
                continue
            p=(f.parent/unquote(u.path)).resolve()
            if assets.allowed(p) and p.is_file(): assets.image(img,p)
            else: warn(f,f'Image missing or outside root: {src}')
        for a in soup.find_all('a',href=True):
            href=a['href']; u=urlsplit(href)
            if u.scheme or u.netloc:
                if u.scheme in ('http','https'): a['target']='_blank'; a['rel']='noopener noreferrer'
                continue
            p=(f.parent/unquote(u.path)).resolve() if u.path else f
            if p.is_dir(): p=next((p/n for n in ('README.md','index.md') if p/n in paths),p)
            anchor=unquote(u.fragment)
            if p in paths:
                if anchor:
                    resolved=headings_by_file[p].get(anchor) or headings_by_file[p].get(slug(anchor))
                    if not resolved: warn(f,f'Unresolved anchor: {href}')
                    anchor=resolved or slug(anchor)
                a['href']=route(paths[p],anchor)
            elif assets.allowed(p) and p.is_file() and p.suffix.lower() not in EXTS:
                assets.attach(a,p); attached.add(p)
            else: warn(f,f'Unresolved local link: {href}')
        if not args.no_implicit_assets:
            for code in list(soup.find_all('code')):
                if code.find_parent(['pre','a','h1','h2','h3','h4','h5','h6']): continue
                mention=code.get_text().strip(); matches=assets.resolve_mention(mention,f)
                if not matches: continue
                attached.update(matches); assets.implicit_files+=len(matches)
                if not mention.replace('\\','/').endswith('/') and len(matches)==1:
                    link=soup.new_tag('a'); assets.attach(link,matches[0]); code.wrap(link)
        if attached:
            assets.catalog(soup,attached)
            page=next(p for p in pages if p['path']==paths[f]); page['headings'].append({'id':'allegati-incorporati','text':'Allegati incorporati','level':2})
            page['text']+=' '+ ' '.join(p.relative_to(root).as_posix() for p in sorted(attached))
        for pre in soup.find_all('pre'):
            code=pre.find('code'); lang=''
            if code:
                lang=next((v[9:] for v in code.get('class',[]) if v.startswith('language-')),'')
                if lang.lower()=='mermaid' and not args.no_mermaid:
                    diagram_count+=1
                    pre['class']=['mermaid-source']
                try:
                    if lang and not (lang.lower()=='mermaid' and not args.no_mermaid):
                        formatted=highlight(code.get_text(),get_lexer_by_name(lang),HtmlFormatter(nowrap=True))
                        code.clear(); code.append(BeautifulSoup(formatted,'html.parser'))
                except ClassNotFound: pass
            wrapper=soup.new_tag('div',attrs={'class':'codeblock'}); pre.wrap(wrapper)
            bar=soup.new_tag('div',attrs={'class':'codebar'}); bar.string=lang or 'testo'
            button=soup.new_tag('button',attrs={'type':'button','class':'copy'}); button.string='Copia'; bar.append(button); pre.insert_before(bar)
            if lang.lower()=='mermaid' and not args.no_mermaid:
                wrapper['class']=['codeblock','mermaid-block']; view=soup.new_tag('div',attrs={'class':'mermaid-view','aria-live':'polite'}); view.string='Rendering diagramma…'; bar.insert_after(view)
                toggle=soup.new_tag('button',attrs={'type':'button','class':'toggle-source'}); toggle.string='Sorgente'; bar.append(toggle); pre['hidden']='' 
        for table in soup.find_all('table'):
            table.wrap(soup.new_tag('div',attrs={'class':'tablewrap'}))
    for source,name in sorted(set(assets.ambiguous)):
        warnings.append(f'{source}: riferimento implicito ambiguo: {name}; usare il percorso relativo')
    render_stats={}
    if args.render_mode=='static' and (math_count or diagram_count):
        print(f'[SVG] Pre-rendering: {math_count} formule, {diagram_count} diagrammi (tema chiaro e scuro)',flush=True)
        try: render_stats=prerender(soups,warn,args.browser_path)
        except RuntimeError as e: ap.error(str(e))
        for k in ('math','mermaid'):
            r=render_stats[k]
            if r['count']: print(f"  {k}: {r['rendered']}/{r['count']} SVG, libreria evitata {r['library_bytes']:,} byte, SVG {r['svg_bytes']:,} byte, errori {r['failed']}",flush=True)
        print(f"  Tempo rendering: {render_stats['seconds']:.2f}s",flush=True)
    if warnings:
        for w in warnings: print('WARNING:',w,file=sys.stderr)
        if args.strict: ap.error('Strict mode: unresolved references or non-offline resources')
    home=args.home or pages[0]['path']
    if home not in paths.values(): ap.error(f'Home not found: {home}')
    raw_templates='\n'.join(f'<template id="p{i}">{soups[f]}</template>' for i,f in enumerate(files))
    templates=''.join(f'<template id="p{i}">{compact_markup(soups[f])}</template>' for i,f in enumerate(files)) if not args.no_minify else raw_templates
    features=[p for p in pages if p['meta']['type']=='feature']
    if features and not args.no_dashboard:
        pages.append({'path':'_features','title':'Funzionalità','headings':[],'text':'Dashboard funzionalità stato servizio dipendenze','meta':{},'dashboard':True})
        dashboard=f'<template id="p{len(files)}"><h1>Funzionalità</h1><div id="feature-dashboard"></div></template>'
        templates+=dashboard; raw_templates+=dashboard
    navigation=build_navigation(pages)
    nav_order=navigation_order(navigation)
    assert len(nav_order)==len(pages) and set(nav_order)=={p['path'] for p in pages}, 'Navigation coverage mismatch'
    mermaid_payload=''
    if diagram_count and args.render_mode=='runtime':
        bundle=Path(__file__).resolve().parent/'vendor'/'mermaid.bundle.js'
        if not bundle.is_file(): ap.error('Missing vendor/mermaid.bundle.js; use --no-mermaid to build without it')
        script=bundle.read_text(encoding='utf-8')
        if re.search(r'</script',script,re.I): ap.error('Unsafe script closing tag in Mermaid bundle')
        mermaid_payload='<script type="text/plain" id="mermaid-lib">'+script+'</script>'
    math_payload=''
    if math_count and args.render_mode=='runtime':
        bundle=Path(__file__).resolve().parent/'vendor'/'mathjax-tex-svg.js'
        if not bundle.is_file(): ap.error('Missing vendor/mathjax-tex-svg.js; use --no-math to build without it')
        script=bundle.read_text(encoding='utf-8')
        if re.search(r'</script',script,re.I): ap.error('Unsafe script closing tag in MathJax bundle')
        math_payload='<script type="text/plain" id="mathjax-lib">'+script+'</script>'
    css=HtmlFormatter(style='default').get_style_defs('.codeblock')+'\n'+HtmlFormatter(style='monokai').get_style_defs('[data-theme="dark"] .codeblock')
    print(f'[3/4] Incorporazione: {len(assets.paths)} file, {len(assets.entries)} contenuti distinti, {len(set().union(*attachments.values()))} allegati, {diagram_count} diagrammi Mermaid, {math_count} formule LaTeX')
    shell=HTML.replace('@@TITLE@@',html.escape(args.title)).replace('@@COUNT@@',str(len(files))).replace('@@DATE@@',datetime.now().strftime('%d/%m/%Y %H:%M')).replace('@@PYGMENTS@@',css)
    if args.render_mode=='static':
        shell=re.sub(r'let mermaidSeq=0,renderGeneration=0,mermaidQueue=Promise.resolve\(\);.*?(?=let spyFrame=0)','function renderDiagrams(){}\nfunction renderMath(){}\n',shell,flags=re.S)
    def assemble(shell,templates,compact):
        payload={'@@NAV@@':js_json(navigation,compact),'@@PAGES@@':js_json(pages,compact),'@@ASSETS@@':js_json(assets.entries,compact),'@@HOME@@':js_json(home,compact),'@@TEMPLATES@@':templates,'@@MERMAID@@':mermaid_payload,'@@MATH@@':math_payload}
        return re.sub(r'@@(?:NAV|PAGES|ASSETS|HOME|TEMPLATES|MERMAID|MATH)@@',lambda m:payload[m.group()],shell)
    raw=assemble(shell,raw_templates,False)
    stats={}; engines={}
    if not args.no_minify: shell,stats,engines=minify_shell(shell)
    result=assemble(shell,templates,not args.no_minify)
    raw_size=len(raw.encode()); min_size=len(result.encode()); gz_size=None; mode='none'
    print('[4/4] Ottimizzazione e scrittura…')
    if args.zopfli_iterations < 1: ap.error('--zopfli-iterations deve essere >= 1')
    result,chosen,candidates,gz_size=optimize(result,args.title,args.compression,args.profile,args.encoding,args.zopfli_iterations,args.zstd_decoder)
    mode=chosen['algorithm']
    final_size=len(result.encode())
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_bytes(result.encode('utf-8'))
    def line(label,b,a):
        print(f'  {label:<26} {b:>12,} -> {a:>12,} byte | risparmio {100*(1-a/b) if b else 0:6.2f}%')
    for label,k in [('CSS','css'),('JavaScript portale','js')]:
        if k+'_before' in stats: line(label,stats[k+'_before'],stats[k+'_after'])
    line('Template HTML',len(raw_templates.encode()),len(templates.encode()))
    line('HTML minificato',raw_size,min_size); line('Compressione '+mode,min_size,final_size); line('TOTALE',raw_size,final_size)
    print('  Minificatori:',engines or 'disabilitati')
    if chosen['payload_bytes'] is not None:
        print(f"  Payload {mode} (prima di {chosen['encoding']}): {chosen['payload_bytes']:,} byte")
        print(f"  Payload codificato ({chosen['encoding']}): {chosen['encoded_bytes']:,} byte")
        print(f"  Overhead contenitore (decoder incluso): {chosen['wrapper_overhead_bytes']:,} byte")
    else: print('  Payload: nessuna compressione/codifica')
    manifest={'version':'4.3.2','explicit_anchors':anchor_count,'inline_properties_resolved':property_count,'render_mode':args.render_mode,'rendering':render_stats,'math_formulas':math_count,'math_renderer':'MathJax 3.2.1 SVG' if math_count else None,'selected_payload_bytes':chosen['payload_bytes'],'selected_encoded_bytes':chosen.get('encoded_bytes'),'selected_wrapper_overhead_bytes':chosen.get('wrapper_overhead_bytes'),'compression_profile':args.profile,'payload_encoding':chosen['encoding'],'selected_candidate':chosen,'compression_candidates':candidates,'documents':len(files),'warnings':warnings,'mermaid_diagrams':diagram_count,'features':len(features),'navigation_sections':len(navigation),'navigation_pages':len(nav_order),'raw_html_bytes':raw_size,'minified_html_bytes':min_size,'gzip_candidate_payload_bytes':gz_size,'gzip_payload_bytes':gz_size,'final_html_bytes':final_size,'compression':mode,'total_reduction_percent':round(100*(1-final_size/raw_size),4),'minifiers':engines,'component_sizes':stats,'asset_paths':len(assets.paths),'unique_asset_payloads':len(assets.entries),'original_asset_bytes':sum(assets.entries[v]['size'] for v in assets.paths.values()),'attachments':len(set().union(*attachments.values())),'assets':[{'path':p.relative_to(root).as_posix(),'id':v,'bytes':assets.entries[v]['size'],'sha256':assets.entries[v]['sha256'],'attachment':any(p in x for x in attachments.values())} for p,v in sorted(assets.paths.items())]}
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True); args.report.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8'); print('  Report:',args.report.resolve())
    print(f'OK: {out.resolve()} | {final_size:,} byte | {len(warnings)} avvisi')

HTML=r'''<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="generator" content="md-portal-python 4.3.1"><title>@@TITLE@@</title>
<script>try{document.documentElement.dataset.theme=localStorage.getItem('mdportal-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}catch(e){}</script>
<style>
:root{--bg:#fff;--side:#f7f8fa;--soft:#f1f4f7;--fg:#202833;--muted:#657184;--line:#dce2e9;--accent:#245cc6;--active:#e3edff;--width:300px;color-scheme:light}
:root[data-theme=dark]{--bg:#171b22;--side:#1d222b;--soft:#252c36;--fg:#e2e8f0;--muted:#a0aaba;--line:#343e4b;--accent:#82b0ff;--active:#243b60;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 system-ui,-apple-system,Segoe UI,sans-serif}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}button,input{font:inherit;color:inherit}button{cursor:pointer;background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:5px 10px}button:hover{background:var(--soft)}button:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--accent)}header{position:fixed;top:0;left:0;right:0;height:56px;z-index:10;display:flex;align-items:center;gap:10px;padding:0 18px;background:var(--bg);border-bottom:1px solid var(--line)}.brand{font-weight:700;color:var(--fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.spacer{flex:1}.sidebar{position:fixed;top:56px;bottom:0;left:0;width:var(--width);background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column;z-index:8}.search{padding:14px 12px 8px}.search input{width:100%;border:1px solid var(--line);background:var(--bg);border-radius:8px;padding:9px 10px;outline:none}.search input:focus{border-color:var(--accent)}.tools{display:flex;gap:6px;padding:0 12px 8px}.tools button{font-size:11px}.navscroll{overflow:auto;flex:1;padding:0 8px 18px}.navlink,summary{display:block;padding:6px 9px;border-radius:5px;line-height:1.4;overflow-wrap:anywhere;color:var(--fg);font-size:13px}.navlink:hover,summary:hover{background:var(--soft);text-decoration:none}.navlink.active{background:var(--active);color:var(--accent);font-weight:650}summary{display:list-item;cursor:pointer;font-weight:650}details>div{margin-left:12px;border-left:1px solid var(--line);padding-left:5px}.foot{font-size:11px;color:var(--muted);padding:10px 13px;border-top:1px solid var(--line)}.resizer{position:absolute;right:-4px;top:0;bottom:0;width:8px;cursor:col-resize;touch-action:none}.main{margin-left:var(--width);padding:82px 28px 60px;display:grid;grid-template-columns:minmax(0,1fr) 215px;gap:32px;max-width:1500px}.article{min-width:0;max-width:950px;width:100%;margin:auto}.crumb{color:var(--muted);font-size:12px;overflow-wrap:anywhere}.doc h1{font-size:2rem;line-height:1.2;margin:12px 0 26px;letter-spacing:-.035em}.doc h2{font-size:1.48rem;border-bottom:1px solid var(--line);padding-bottom:8px;margin-top:35px}.doc h3{font-size:1.19rem;margin-top:25px}.doc h4,.doc h5,.doc h6{margin-top:22px}.doc :is(h1,h2,h3,h4,h5,h6){scroll-margin-top:75px}.doc p{margin:12px 0}.doc li{margin:4px 0}.doc img{max-width:100%;height:auto}.doc p>img:only-child,.doc p>a:only-child>img{display:block;margin:18px auto}.doc blockquote{border-left:3px solid var(--accent);background:var(--soft);margin:14px 0;padding:4px 16px}.doc hr{border:0;border-top:1px solid var(--line)}.doc code{font: .88em ui-monospace,Consolas,monospace;background:var(--soft);border-radius:4px;padding:2px 5px;overflow-wrap:anywhere}.codeblock{border:1px solid var(--line);border-radius:8px;overflow:hidden;margin:16px 0}.codebar{display:flex;align-items:center;justify-content:space-between;background:var(--soft);font-size:12px;color:var(--muted);padding:4px 10px}.codebar button{font-size:11px}.codeblock pre{overflow:auto;margin:0;padding:14px;background:var(--soft)}.codeblock code{background:none;padding:0;overflow-wrap:normal}.tablewrap{overflow:auto;margin:15px 0}.doc table{border-collapse:collapse;width:100%;font-size:14px}.doc th,.doc td{border:1px solid var(--line);padding:8px 11px;text-align:left}.doc th{background:var(--soft)}.doc tbody tr:nth-child(even){background:var(--side)}.toc{position:sticky;top:80px;align-self:start;max-height:calc(100vh - 100px);overflow:auto;border-left:1px solid var(--line);padding:0 0 0 16px;font-size:12px}.toc strong{font-size:13px}.toc a{display:block;color:var(--muted);padding:4px 0;line-height:1.4;overflow-wrap:anywhere}.toc a:hover{color:var(--accent)}.pager{display:flex;justify-content:space-between;gap:15px;margin-top:36px;border-top:1px solid var(--line);padding-top:15px;font-size:13px}.result{display:block;padding:10px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.result strong{display:block;font-size:13px}.result small{display:block;color:var(--muted);font-size:11px}.result span{display:block;font-size:12px;color:var(--fg)}.result:hover{background:var(--soft);text-decoration:none}#menu{display:none}.backdrop{display:none}[hidden]{display:none!important}@@PYGMENTS@@
@media(max-width:1120px){.main{grid-template-columns:minmax(0,1fr)}.toc{display:none}}@media(max-width:760px){#menu{display:block}.sidebar{transform:translateX(-100%);width:min(86vw,var(--width));transition:transform .15s}body.open .sidebar{transform:none}body.open .backdrop{display:block;position:fixed;inset:56px 0 0;background:#0006;z-index:7}.main{margin:0;padding:75px 18px 35px}.resizer{display:none}.doc h1{font-size:1.7rem}}@media print{header,.sidebar,.toc,.pager,.crumb,.codebar{display:none!important}.main{display:block;margin:0;padding:0}.article{max-width:none}.doc{color:#000;background:#fff}.doc a{color:#000}.doc table{font-size:11px}}

.metadata{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:12px 0 24px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--side);font-size:13px}.meta-field{color:var(--muted)}.meta-deps{flex-basis:100%;display:flex;gap:5px;align-items:center;flex-wrap:wrap}.status-badge,.dependency{display:inline-block;padding:2px 7px;border:1px solid var(--line);border-radius:5px;font-size:12px;white-space:nowrap;margin:2px}.status-dot{display:inline-block;width:9px;height:9px;margin-right:7px;border-radius:50%;background:#8392a5}.todo{--state:#78869a}.doing{--state:#c28a19}.done{--state:#26965e}.blocked{--state:#cc4b4b}.cancelled{--state:#9765b0}.unknown{--state:#8392a5}.status-dot{background:var(--state)}.status-badge,.dependency{border-left:4px solid var(--state,var(--line))}.nav-id{color:var(--muted);font-size:10px}.feature-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(98px,1fr));gap:8px;margin:15px 0}.stat{display:flex;flex-direction:column;align-items:flex-start;padding:10px;line-height:1.3}.stat strong{font-size:25px}.stat span{font-size:12px}.stat.selected{border-color:var(--accent);background:var(--active)}.feature-filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.feature-filters input,.feature-filters select{border:1px solid var(--line);border-radius:6px;padding:5px 8px;background:var(--bg);color:var(--fg);font:inherit;max-width:100%}.feature-summary{color:var(--muted);font-size:13px}.mermaid-view{background:var(--bg);padding:18px;overflow:auto;text-align:center}.mermaid-view svg{max-width:100%;height:auto}.diagram-error{color:#ce5252;text-align:left}.mermaid-block pre[hidden]{display:none!important}.mermaid-block{break-inside:avoid}.toggle-source{margin-left:6px}@media print{.feature-filters{display:none}.mermaid-view{overflow:visible}}
.asset-catalog{border-top:1px solid var(--line);margin-top:32px}.asset-catalog li{overflow-wrap:anywhere}.asset-catalog button{font-size:12px;margin-left:10px}.asset-catalog small{color:var(--muted)}dialog.asset-dialog{width:min(1100px,94vw);max-height:85vh;overflow:auto;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:18px}dialog::backdrop{background:#0008}.asset-dialog .tablewrap{max-height:55vh;overflow:auto}.asset-dialog th{position:sticky;top:0;background:var(--soft)}.asset-dialog td,.asset-dialog th{white-space:nowrap;border:1px solid var(--line);padding:5px 8px;font-size:12px}.asset-dialog table{border-collapse:collapse}.asset-dialog input{width:min(350px,90%);margin:8px 0;padding:7px;background:var(--bg);border:1px solid var(--line);border-radius:5px}.asset-dialog h2{overflow-wrap:anywhere}
.math-tex{color:inherit}.math-display{display:block;overflow-x:auto;text-align:center;margin:1em 0;padding:.25em 0}.math-inline{display:inline-block;max-width:100%;vertical-align:middle}.math-tex svg{color:inherit;max-width:none}.math-tex mjx-assistive-mml{position:absolute!important;width:1px!important;height:1px!important;overflow:hidden!important;clip-path:inset(50%);white-space:nowrap!important}.math-tex[data-math-error]{color:var(--fg);white-space:pre-wrap;border-bottom:1px dashed #ce5252}.math-display mjx-container{margin:0!important}.math-inline mjx-container{margin:0!important}@media print{.math-display{overflow:visible}}
.mermaid-static .diagram-dark{display:none}[data-theme="dark"] .mermaid-static .diagram-light{display:none}[data-theme="dark"] .mermaid-static .diagram-dark{display:inline-block}.math-static svg{fill:currentColor;stroke:currentColor;display:inline-block;vertical-align:middle}.math-static svg[style]{vertical-align:baseline}.math-static.math-display svg{vertical-align:middle}
.md-explicit-anchor{scroll-margin-top:86px} .doc > .md-explicit-anchor{display:block;height:0}
/* Structured navigation and scroll-aware contents */
:root{--width:326px;--mark:#ffe6a0}:root[data-theme=dark]{--mark:#705a20}
.search{position:relative;padding:10px 10px 8px}.search input{font-size:15px;padding:9px 32px 9px 12px}.search kbd{position:absolute;right:21px;top:21px;color:var(--muted);font-size:12px;border:1px solid var(--line);border-radius:4px;padding:0 5px;pointer-events:none}.search:focus-within kbd{display:none}.navscroll{padding:0 7px 18px}.navsection{margin:13px 0 19px}.navsection-title,.toc-title{display:block;text-transform:uppercase;letter-spacing:.065em;color:var(--muted);font-size:12px;font-weight:700;margin:0 9px 7px}.navsection ul{list-style:none;padding:0;margin:0}.navsection li>ul{display:none;border-left:1px solid var(--line);margin-left:11px;padding-left:11px}.navsection li.expanded>ul{display:block}.navrow{display:flex;align-items:flex-start;border-radius:7px;min-height:28px}.navrow:hover{background:var(--soft)}.navrow:has(>.active){background:var(--active)}.navtoggle{border:0;background:none;color:var(--muted);padding:0;flex:0 0 25px;width:25px;height:29px;font-size:24px;line-height:25px;text-align:center}.navtoggle:hover{background:none;color:var(--fg)}.expanded>.navrow>.navtoggle{transform:rotate(90deg)}.navrow .navlink{display:block;flex:1;min-width:0;font-size:15px;line-height:1.45;padding:4px 7px 4px 0;border:0;background:none;text-align:left;color:var(--fg);font-weight:400;cursor:pointer}.navrow .navlink.group{font-weight:650}.navrow .navlink.active{color:var(--accent);font-weight:650}.navfeature{display:inline-block;margin-right:6px;position:relative;top:-1px;padding:1px 6px;border-radius:5px;font:600 11px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;font-variant-numeric:tabular-nums;letter-spacing:.01em;white-space:nowrap;vertical-align:baseline;color:var(--badge-fg,var(--muted));background:var(--badge-bg,var(--soft));box-shadow:inset 0 0 0 1px var(--badge-border,var(--line))}.navfeature.todo{--badge-fg:#343a40;--badge-bg:#f1f3f5;--badge-border:#b9bfc5}.navfeature.doing{--badge-fg:#785000;--badge-bg:#fff3bf;--badge-border:#e2ae56}.navfeature.done{--badge-fg:#1b4332;--badge-bg:#d3f9d8;--badge-border:#85c591}.navfeature.blocked{--badge-fg:#8a1420;--badge-bg:#ffe3e3;--badge-border:#df9696}.navfeature.cancelled{--badge-fg:#6e3e91;--badge-bg:#f2e7fb;--badge-border:#c5a7da}[data-theme=dark] .navfeature.todo{--badge-fg:#dee2e6;--badge-bg:#303640;--badge-border:#647080}[data-theme=dark] .navfeature.doing{--badge-fg:#ffec99;--badge-bg:#49371f;--badge-border:#a87b38}[data-theme=dark] .navfeature.done{--badge-fg:#b2f2bb;--badge-bg:#203d2c;--badge-border:#558e62}[data-theme=dark] .navfeature.blocked{--badge-fg:#ffc9c9;--badge-bg:#482a30;--badge-border:#b75f67}[data-theme=dark] .navfeature.cancelled{--badge-fg:#dfc1f5;--badge-bg:#3c2c4c;--badge-border:#9873b4}.navfeature-code{font-weight:400;opacity:.78}.navlink.active .navfeature{box-shadow:inset 0 0 0 1.5px var(--state,var(--accent))}.toc{border:0;padding:0;font-size:13px}.toc-title{margin:0 0 10px}.toc .toclink{padding:4px 6px;border-left:2px solid var(--line);line-height:1.45;font-size:13px}.toc .toclink.active{color:var(--accent);border-left-color:var(--accent)}.toc .toclink:hover{text-decoration:none;color:var(--accent)}.main{grid-template-columns:minmax(0,1fr) 235px}.article{align-self:start;margin:0 auto}.result{border:0;border-radius:7px;margin:2px 0;padding:8px 10px}.result.sel{background:var(--active)}.result strong{color:var(--fg);font-size:14px}.result small{margin-top:2px;font-size:11px}.result span{margin-top:3px;font-size:13px}.result mark{background:var(--mark);color:inherit}::highlight(md-search){background:var(--mark);color:inherit}.results-head{padding:8px 10px;font-size:12px;color:var(--muted)}.crumb span{white-space:pre-wrap}.crumb a{color:var(--muted)}
@media(max-width:1120px){.main{grid-template-columns:minmax(0,1fr)}}

</style></head><body>
<header><button id="menu" aria-label="Apri indice">☰</button><a class="brand" id="brand" href="#">@@TITLE@@</a><span class="spacer"></span><button id="print">Stampa</button><button id="theme" aria-label="Cambia tema">◐ Tema</button></header>
<aside class="sidebar"><div class="search"><input id="search" type="search" placeholder="Cerca nella documentazione" aria-label="Cerca nella documentazione" autocomplete="off"><kbd>/</kbd></div><div class="tools"><button id="expand">Espandi</button><button id="collapse">Comprimi</button></div><div class="navscroll"><nav id="nav" aria-label="Documenti"></nav><div id="results" hidden></div></div><div class="foot">@@COUNT@@ documenti · @@DATE@@<br>Offline · MD Portal</div><div class="resizer" id="resizer" aria-hidden="true"></div></aside><div class="backdrop" id="backdrop"></div>
<main class="main"><article class="article"><div class="crumb" id="crumb"></div><div class="doc" id="content"></div><div class="pager" id="pager"></div></article><aside class="toc" id="toc" aria-label="In questa pagina"></aside></main>
@@TEMPLATES@@
@@MERMAID@@
@@MATH@@
<script id="asset-data" type="application/json">@@ASSETS@@</script><script id="nav-data" type="application/json">@@NAV@@</script><script id="page-data" type="application/json">@@PAGES@@</script><script>
'use strict'; const pages=JSON.parse(document.getElementById('page-data').textContent),home=@@HOME@@;
const $=id=>document.getElementById(id), nav=$('nav'), results=$('results'), search=$('search'), content=$('content');
const route=(p,h='')=>'#/'+p.split('/').map(encodeURIComponent).join('/')+(h?'#'+encodeURIComponent(h):'');
const fold=s=>{const lower=String(s).toLowerCase(), f=lower.normalize('NFD').replace(/\p{M}/gu,'');return f.length===lower.length?f:lower};
const el=(tag,cls='',text='')=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text)n.textContent=text;return n};
const makeLink=(p,h='',label=p.title,cls='')=>{const a=el('a',cls,label);a.href=route(p.path,h);return a};
const assets=JSON.parse($('asset-data').textContent),assetUrls=new Map();
function assetUrl(id){if(!assetUrls.has(id)){const a=assets[id];const bytes=Uint8Array.from(atob(a.data),c=>c.charCodeAt(0));assetUrls.set(id,URL.createObjectURL(new Blob([bytes],{type:a.mime})))}return assetUrls.get(id)}
function hydrateAssets(){for(const img of content.querySelectorAll('[data-image]'))img.src=assetUrl(img.dataset.image);for(const a of content.querySelectorAll('[data-asset]'))a.href=assetUrl(a.dataset.asset)}
function previewAsset(id,name){const a=assets[id],p=a.preview;if(!p)return;const dialog=el('dialog','asset-dialog');const close=el('button','','Chiudi');close.onclick=()=>dialog.close();const h=el('h2','',name);const dl=el('a','','Scarica originale');dl.href=assetUrl(id);dl.download=name;const info=el('p','',Math.max(0,p.total-1)+' righe dati · prime '+Math.max(0,p.rows.length-1)+' in anteprima · separatore '+JSON.stringify(p.delimiter));const search=el('input');search.type='search';search.placeholder='Filtra righe in anteprima';const wrap=el('div','tablewrap'),table=el('table'),head=el('thead'),hr=el('tr'),body=el('tbody');for(const v of p.rows[0])hr.append(el('th','',v));head.append(hr);table.append(head,body);wrap.append(table);function draw(){body.replaceChildren();const q=fold(search.value);for(const row of p.rows.slice(1)){if(q&&!row.some(v=>fold(v).includes(q)))continue;const tr=el('tr');for(const v of row)tr.append(el('td','',v));body.append(tr)}}search.oninput=draw;draw();dialog.append(close,h,dl,info,search,wrap);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal()}
content.addEventListener('click',e=>{const b=e.target.closest('[data-preview]');if(b)previewAsset(b.dataset.preview,b.dataset.name)});
const statusClass=s=>({'da fare':'todo','in corso':'doing','fatta':'done','bloccata':'blocked','abbandonata':'cancelled'}[s]||'unknown');
function statusBadge(s){return el('span','status-badge '+statusClass(s),s||'non specificato')}
const byPath=new Map(pages.map((p,i)=>[p.path,{p,i}]));
const sections=JSON.parse($('nav-data').textContent), links=new Map(), trails=new Map(), ordered=[];
function drawNode(node,trail){const li=el('li',node.open?'expanded':'');const row=el('div','navrow');const kids=node.children?.length;const toggle=el(kids?'button':'span','navtoggle',kids?'›':'');if(kids){toggle.type='button';toggle.setAttribute('aria-label','Espandi o comprimi '+node.label);toggle.setAttribute('aria-expanded',String(!!node.open));toggle.onclick=()=>setOpen(li,!li.classList.contains('expanded'))}row.append(toggle);const p=node.page&&byPath.get(node.page)?.p;const a=p?makeLink(p,'',node.label,'navlink'):el('button','navlink',node.label);if(kids)a.classList.add('group');if(!p){a.type='button';a.onclick=()=>setOpen(li,!li.classList.contains('expanded'))}else{a.title=p.path;links.set(p.path,a);trails.set(p.path,trail.concat([{label:node.label,page:node.page}]));ordered.push(p);if(node.id){const id=el('span','navfeature '+statusClass(node.status));const m=/^(.+?-)(\d+)$/.exec(node.id);if(m)id.append(el('span','navfeature-code',m[1]),document.createTextNode(m[2]));else id.textContent=node.id;id.title=node.id+(node.status?' · '+node.status:'');if(a.textContent.toUpperCase().startsWith(node.id.toUpperCase()))a.textContent=a.textContent.slice(node.id.length).replace(/^\s*[·:—–-]?\s*/, '');a.prepend(id)}else if(node.status)a.prepend(el('span','status-dot '+statusClass(node.status)))}row.append(a);li.append(row);if(kids){const ul=el('ul');for(const c of node.children)ul.append(drawNode(c,trail.concat([{label:node.label,page:node.page}])));li.append(ul)}return li}
function setOpen(li,on){li.classList.toggle('expanded',on);li.querySelector(':scope > .navrow > button.navtoggle')?.setAttribute('aria-expanded',String(on))}
for(const section of sections){const box=el('section','navsection');box.append(el('div','navsection-title',section.label));const ul=el('ul');for(const n of section.children)ul.append(drawNode(n,[{label:section.label}]));box.append(ul);nav.append(box)}
function reveal(p){for(const a of links.values()){a.classList.remove('active');a.removeAttribute('aria-current')}const a=links.get(p.path);if(!a)return;a.classList.add('active');a.setAttribute('aria-current','page');for(let n=a.closest('li');n;n=n.parentElement.closest('li')){if(n.querySelector(':scope > ul'))setOpen(n,true)}if(!nav.hidden){const pane=nav.parentElement,r=a.getBoundingClientRect(),b=pane.getBoundingClientRect();if(r.top<b.top||r.bottom>b.bottom)pane.scrollTop+=r.top-b.top-b.height/2}}
function crumbs(p){const root=$('crumb');root.replaceChildren();const list=trails.get(p.path)||[{label:p.title}];list.forEach((n,i)=>{if(i)root.append(el('span','',' / '));root.append(n.page&&i<list.length-1?makeLink(byPath.get(n.page).p,'',n.label):el('span','',n.label))})}
$('brand').href=route(home);let active=-1;
const features=pages.filter(p=>p.meta?.type==='feature');
function dependencyLink(id,service){const key=id.toLowerCase();const hits=features.filter(p=>p.meta.id.toLowerCase()===key||(`${p.meta.service}:${p.meta.id}`).toLowerCase()===key);const local=hits.filter(p=>p.meta.service===service);const hit=local.length===1?local[0]:hits.length===1?hits[0]:null;return hit?makeLink(hit,'',id,'dependency '+statusClass(hit.meta.status)):el('span','dependency',id)}
function showMetadata(p){const m=p.meta||{};const fields=[['ID',m.id],['Servizio',m.service],['Versione',m.version],['Autore',m.author],['Responsabile',m.owner]];if(!fields.some(x=>x[1])&&!m.status&&!m.tags?.length&&!m.depends_on?.length&&!m.enables?.length)return;const box=el('div','metadata');if(m.status)box.append(statusBadge(m.status));for(const [k,v] of fields)if(v)box.append(el('span','meta-field',k+': '+v));for(const tag of m.tags||[])box.append(el('span','dependency',tag));for(const [k,label] of [['depends_on','Dipende da'],['enables','Abilita']])if(m[k]?.length){const row=el('div','meta-deps');row.append(el('strong','',label+': '));for(const id of m[k])row.append(dependencyLink(id,m.service));box.append(row)}const h=content.querySelector('h1');if(h)h.after(box);else content.prepend(box)}
function buildDashboard(){const root=$('feature-dashboard');const statusList=[...new Set(['da fare','in corso','fatta','bloccata','abbandonata',...features.map(p=>p.meta.status)])];const services=[...new Set(features.map(p=>p.meta.service))].sort();const stats=el('div','feature-stats');root.append(stats);const filters=el('div','feature-filters');root.append(filters);function select(label,options){const wrap=el('label','',label+' '),s=el('select');for(const [v,t] of options){const o=el('option','',t);o.value=v;s.append(o)}wrap.append(s);filters.append(wrap);return s}const svc=select('Servizio',[['','Tutti'],...services.map(s=>[s,s])]),st=select('Stato',[['','Tutti'],...statusList.map(s=>[s,s])]);const q=el('input');q.type='search';q.placeholder='ID, titolo, tag…';q.setAttribute('aria-label','Cerca funzionalità');filters.append(q);const summary=el('p','feature-summary'),list=el('div');root.append(summary,list);
function update(){const base=features.filter(p=>(!svc.value||p.meta.service===svc.value)&&(!q.value||fold(p.meta.id+' '+p.title+' '+p.meta.tags.join(' ')).includes(fold(q.value.trim()))));const filtered=base.filter(p=>!st.value||p.meta.status===st.value);stats.replaceChildren();for(const [name,count,value] of [['Totale',base.length,''],...statusList.map(s=>[s,base.filter(p=>p.meta.status===s).length,s])]){const b=el('button','stat '+(st.value===value?'selected':''));b.append(el('strong','',String(count)),el('span','',name));b.onclick=()=>{st.value=value;update()};stats.append(b)}summary.textContent=filtered.length+' di '+base.length+' funzionalità · '+(base.length?Math.round(100*base.filter(p=>p.meta.status==='fatta').length/base.length):0)+'% completate (conteggio, non effort)';list.replaceChildren();for(const service of services){const rows=filtered.filter(p=>p.meta.service===service);if(!rows.length)continue;list.append(el('h2','',service+' ('+rows.length+')'));const table=el('table'),head=el('thead'),hr=el('tr');for(const h of ['ID','Titolo','Stato','Versione','Dipende da','Abilita'])hr.append(el('th','',h));head.append(hr);table.append(head);const body=el('tbody');for(const p of rows.sort((a,b)=>a.meta.id.localeCompare(b.meta.id,undefined,{numeric:true})||a.title.localeCompare(b.title))){const m=p.meta,tr=el('tr');for(const v of [makeLink(p,'',m.id||'—'),makeLink(p),statusBadge(m.status),el('span','',m.version||'—')]){const td=el('td');td.append(v);tr.append(td)}for(const field of ['depends_on','enables']){const td=el('td');for(const id of m[field])td.append(dependencyLink(id,m.service));if(!m[field].length)td.textContent='—';tr.append(td)}body.append(tr)}table.append(body);const wrap=el('div','tablewrap');wrap.append(table);list.append(wrap)}if(!filtered.length)list.append(el('p','','Nessuna funzionalità corrispondente.'))}for(const f of [svc,st])f.onchange=update;q.oninput=update;update()}
let mermaidSeq=0,renderGeneration=0,mermaidQueue=Promise.resolve();
function renderDiagrams(){const generation=++renderGeneration,blocks=[...content.querySelectorAll('.mermaid-block:not(.mermaid-static)')];if(!blocks.length)return;mermaidQueue=mermaidQueue.catch(()=>{}).then(async()=>{if(generation!==renderGeneration)return;try{if(!window.mermaid){const lib=$('mermaid-lib');if(!lib)throw Error('Libreria Mermaid mancante');const script=el('script');script.textContent=lib.textContent;document.head.append(script)}if(!window.mermaid)throw Error('Libreria Mermaid non caricata');window.mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:document.documentElement.dataset.theme==='dark'?'dark':'default',suppressErrorRendering:true});for(const block of blocks){if(generation!==renderGeneration||!block.isConnected)return;const view=block.querySelector('.mermaid-view'),pre=block.querySelector('pre');try{const result=await window.mermaid.render('mdp-mermaid-'+(++mermaidSeq),pre.textContent);if(generation===renderGeneration&&block.isConnected){view.innerHTML=result.svg;view.classList.remove('diagram-error')}}catch(e){if(generation===renderGeneration&&block.isConnected){view.textContent='Diagramma non renderizzabile: '+String(e?.message||e).split('\n')[0];view.classList.add('diagram-error');pre.hidden=false}}}}catch(e){for(const block of blocks){if(!block.isConnected)continue;block.querySelector('.mermaid-view').textContent=String(e);block.querySelector('pre').hidden=false}}})}
let mathReady=null,mathQueue=Promise.resolve(),mathGeneration=0;
function loadMath(){if(!mathReady){mathReady=Promise.resolve().then(async()=>{const lib=$('mathjax-lib');if(!lib)throw Error('Libreria MathJax mancante');window.MathJax={startup:{typeset:false},tex:{packages:['base','ams','newcommand','textmacros'],maxBuffer:100000},svg:{fontCache:'none'},options:{enableMenu:false}};const script=el('script');script.textContent=lib.textContent;document.head.append(script);await window.MathJax.startup.promise;if(typeof window.MathJax.tex2svgPromise!=='function')throw Error('MathJax non disponibile');return window.MathJax})}return mathReady}
function renderMath(){const generation=++mathGeneration,items=[...content.querySelectorAll('.math-tex:not(.math-static)')];if(!items.length)return;mathQueue=mathQueue.catch(()=>{}).then(async()=>{if(generation!==mathGeneration)return;let mj;try{mj=await loadMath()}catch(e){for(const item of items){item.dataset.mathError='true';item.title=String(e)}return}for(const item of items){if(generation!==mathGeneration||!item.isConnected)return;const source=item.textContent;try{const node=await mj.tex2svgPromise(source,{display:item.dataset.display==='true'});if(node.querySelector('[data-mml-node="merror"]'))throw Error('Formula LaTeX non valida');if(generation!==mathGeneration||!item.isConnected)return;item.replaceChildren(node);item.dataset.mathRendered='true';item.setAttribute('aria-label',source)}catch(e){item.dataset.mathError='true';item.title=String(e)}}})}
let spyFrame=0, tocPairs=[],pendingTerms=null,queryTerms=[],selected=-1,lastQuery=null,index=null,timer;
function buildToc(p){const toc=$('toc');toc.replaceChildren(el('strong','toc-title','In questa pagina'));let hs=p.headings.slice();if(hs.length>1&&hs[0].level===1)hs.shift();const min=Math.min(...hs.map(h=>h.level));tocPairs=[];for(const h of hs){const target=content.querySelector('#'+CSS.escape('section-'+h.id));if(!target)continue;const a=makeLink(p,h.id,h.text,'toclink');a.style.paddingLeft=(11+Math.min(4,h.level-min)*13)+'px';toc.append(a);tocPairs.push({a,target})}toc.hidden=!tocPairs.length;toc.scrollTop=0}
function spy(){spyFrame=0;if(!tocPairs.length)return;let current=null;const threshold=document.querySelector('header').getBoundingClientRect().bottom+65;for(const pair of tocPairs){if(pair.target.getBoundingClientRect().top<=threshold)current=pair;else break}if(window.scrollY>0&&Math.ceil(window.scrollY+innerHeight)>=document.documentElement.scrollHeight-3)current=tocPairs.at(-1);const changed=current&&!current.a.classList.contains('active');for(const pair of tocPairs){pair.a.classList.toggle('active',pair===current);if(pair===current)pair.a.setAttribute('aria-current','location');else pair.a.removeAttribute('aria-current')}if(changed){const toc=$('toc'),r=current.a.getBoundingClientRect(),b=toc.getBoundingClientRect();if(r.top<b.top||r.bottom>b.bottom)toc.scrollTop+=r.top-b.top-b.height/2}}
function queueSpy(){if(!spyFrame)spyFrame=requestAnimationFrame(spy)}
window.addEventListener('scroll',queueSpy,{passive:true});window.addEventListener('resize',queueSpy);content.addEventListener('load',queueSpy,true);if(window.ResizeObserver)new ResizeObserver(queueSpy).observe(content);
function clearHits(){if(window.CSS?.highlights)CSS.highlights.delete('md-search')}
function rangesFor(text,terms){const f=fold(text),out=[];for(const t of terms){if(!t)continue;for(let i=f.indexOf(t);i>=0;i=f.indexOf(t,i+t.length))out.push([i,i+t.length])}out.sort((a,b)=>a[0]-b[0]||b[1]-a[1]);const merged=[];for(const r of out){const last=merged.at(-1);if(last&&r[0]<=last[1])last[1]=Math.max(last[1],r[1]);else merged.push(r)}return merged}
function highlightTerms(terms,scroll){clearHits();if(!terms.length||!window.CSS?.highlights||!window.Highlight)return;const ranges=[],walker=document.createTreeWalker(content,NodeFilter.SHOW_TEXT);for(let n=walker.nextNode();n&&ranges.length<5000;n=walker.nextNode()){if(n.parentElement.closest('svg,script,style,[hidden],.codebar'))continue;for(const [a,b] of rangesFor(n.data,terms)){const r=new Range();r.setStart(n,a);r.setEnd(n,b);ranges.push(r);if(ranges.length>=5000)break}}if(!ranges.length)return;CSS.highlights.set('md-search',new Highlight(...ranges));if(scroll){const r=ranges.find(r=>r.getBoundingClientRect().height>0)?.getBoundingClientRect();if(r)window.scrollTo(0,window.scrollY+r.top-innerHeight/3)}}
function show(){let path=home,anchor='';try{if(location.hash.startsWith('#/')){const parts=location.hash.slice(2).split('#');path=decodeURIComponent(parts[0]);anchor=decodeURIComponent(parts[1]||'')}}catch(e){}const {p,i}=byPath.get(path)||byPath.get(home);const changed=i!==active;clearHits();if(changed){content.replaceChildren($('p'+i).content.cloneNode(true));hydrateAssets();if(p.dashboard)buildDashboard();else showMetadata(p);renderDiagrams();renderMath();crumbs(p);buildToc(p);const j=ordered.indexOf(p);$('pager').replaceChildren(j>0?makeLink(ordered[j-1],'','← '+ordered[j-1].title):el('span'),j<ordered.length-1?makeLink(ordered[j+1],'',ordered[j+1].title+' →'):el('span'));reveal(p);active=i;document.title=p.title+' — '+$('brand').textContent}document.body.classList.remove('open');const terms=pendingTerms;pendingTerms=null;requestAnimationFrame(()=>{if(active!==i)return;const h=anchor&&content.querySelector('#'+CSS.escape('section-'+anchor));if(h)h.scrollIntoView();else if(changed||terms)window.scrollTo(0,0);if(terms)highlightTerms(terms,!h);queueSpy()});markResult(p.path)}
function buildIndex(){return pages.map(p=>({p,title:fold(p.title+' '+p.path+' '+(p.meta?.id||'')),text:fold(p.text),head:p.headings.map(h=>({h,txt:fold(h.text)}))}))}
function countOf(hay,t){let count=0;for(let i=hay.indexOf(t);i>=0&&count<30;i=hay.indexOf(t,i+t.length))count++;return count}
function snippet(x,terms){let at=-1;for(const t of terms){at=x.text.indexOf(t);if(at>=0)break}const start=at<0?0:Math.max(0,at-60),end=Math.min(x.p.text.length,at<0?140:at+120),piece=x.p.text.slice(start,end),out=el('span','result-snippet');if(start)out.append('…');let pos=0;for(const [a,b] of rangesFor(piece,terms)){out.append(piece.slice(pos,a),el('mark','',piece.slice(a,b)));pos=b}out.append(piece.slice(pos));if(end<x.p.text.length)out.append('…');return out}
function idHits(q){const m=/^(?:([\w-]+)\s*[:#]\s*)?(US-)?([a-z][a-z0-9-]*?-?|F)(\d+)(?:-(\d+))?$/i.exec(q.trim());if(!m)return [];const [,svc,us,code,num,k]=m;if(k&&!us)return [];const canonical=v=>v.toUpperCase().replace(/0*(\d+)$/,(_,n)=>String(Number(n)));const id=canonical(code+num),out=[];for(const p of features){if(canonical(p.meta.id||'')!==id||svc&&!fold(p.meta.service).endsWith(fold(svc)))continue;if(!us){out.push({p,anchor:'',label:p.title.toUpperCase().startsWith(p.meta.id.toUpperCase())?p.title:p.meta.id+' · '+p.title});continue}for(const h of p.headings){const match=/\bUS-([A-Z][A-Z0-9-]*?\d+)-(\d+)\b/i.exec(h.text);if(match&&canonical(match[1])===id&&(!k||Number(k)===Number(match[2])))out.push({p,anchor:h.id,label:h.text})}}return out}
function markResult(path){const items=[...results.querySelectorAll('.result')];let j=items.findIndex(a=>a.getAttribute('href')===location.hash);if(j<0)j=items.findIndex(a=>a.dataset.page===path);if(j>=0)selectResult(j,false)}
function selectResult(j,scroll=true){const items=[...results.querySelectorAll('.result')];selected=j;items.forEach((a,i)=>{a.classList.toggle('sel',i===j);if(i===j)a.setAttribute('aria-current','true');else a.removeAttribute('aria-current')});if(scroll&&items[j])items[j].scrollIntoView({block:'nearest'})}
function openResult(a){pendingTerms=queryTerms.slice();if(location.hash===a.getAttribute('href'))show();else location.hash=a.getAttribute('href')}
function runSearch(){const q=search.value.trim();if(q===lastQuery)return;lastQuery=q;selected=-1;queryTerms=[...new Set(fold(q).split(/\s+/).filter(t=>t.length>=2))];nav.hidden=!!q;results.hidden=!q;results.replaceChildren();if(!q){clearHits();if(active>=0)reveal(pages[active]);return}const terms=queryTerms;if(!terms.length){results.append(el('div','results-head','Scrivi almeno 2 caratteri'));return}if(!index)index=buildIndex();const ids=idHits(q),seen=new Set(ids.map(x=>x.p.path)),hits=[];for(const x of index){if(seen.has(x.p.path))continue;let score=0,ok=true;for(const t of terms){const title=x.title.includes(t),head=x.head.some(h=>h.txt.includes(t)),n=countOf(x.text,t);if(!title&&!head&&!n){ok=false;break}score+=(title?30:0)+(head?8:0)+n}if(!ok)continue;if(terms.length>1&&x.text.includes(fold(q)))score+=15;const heading=x.head.find(h=>terms.every(t=>h.txt.includes(t)));hits.push({x,heading,score})}hits.sort((a,b)=>b.score-a.score||a.x.p.title.localeCompare(b.x.p.title,'it'));const total=ids.length+hits.length;results.append(el('div','results-head',total?(total>80?'Primi 80 di ':'')+total+(total===1?' risultato':' risultati'):'Nessun risultato'));function add(p,anchor,label,sub,excerpt){const a=makeLink(p,anchor,'','result');a.dataset.page=p.path;a.append(el('strong','',label),el('small','',sub));if(excerpt)a.append(excerpt);a.onclick=e=>{if(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;e.preventDefault();openResult(a)};results.append(a)}for(const r of ids.slice(0,80))add(r.p,r.anchor,r.label,r.p.path,null);for(const {x,heading} of hits.slice(0,Math.max(0,80-ids.length)))add(x.p,heading?.h.id||'',x.p.title,x.p.path+(heading?' · '+heading.h.text:''),snippet(x,terms));nav.parentElement.scrollTop=0}
search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(runSearch,120)});search.addEventListener('keydown',e=>{if(e.key==='Escape'){clearTimeout(timer);search.value='';runSearch();search.blur()}else if(['ArrowDown','ArrowUp','Enter'].includes(e.key)){e.preventDefault();clearTimeout(timer);runSearch();const items=[...results.querySelectorAll('.result')];if(!items.length)return;if(e.key==='Enter'){openResult(items[selected>=0?selected:0]);return}const j=selected<0?(e.key==='ArrowDown'?0:items.length-1):(selected+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;selectResult(j)}});
document.addEventListener('keydown',e=>{if((e.key==='/'&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)&&!document.activeElement.isContentEditable)||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k')){e.preventDefault();document.body.classList.add('open');search.focus();search.select()}});
window.addEventListener('hashchange',show);show();
$('expand').onclick=()=>nav.querySelectorAll('li').forEach(d=>{if(d.querySelector(':scope > ul'))setOpen(d,true)});$('collapse').onclick=()=>nav.querySelectorAll('li').forEach(d=>setOpen(d,false));$('menu').onclick=()=>document.body.classList.toggle('open');$('backdrop').onclick=()=>document.body.classList.remove('open');$('print').onclick=()=>window.print();$('theme').onclick=()=>{const t=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=t;renderDiagrams();try{localStorage.setItem('mdportal-theme',t)}catch(e){}};
try{const w=Number(localStorage.getItem('mdportal-width'));if(w>=200&&w<=650)document.documentElement.style.setProperty('--width',w+'px')}catch(e){}$('resizer').addEventListener('pointerdown',e=>{e.preventDefault();const r=$('resizer');r.setPointerCapture(e.pointerId);r.onpointermove=ev=>{const w=Math.max(200,Math.min(650,innerWidth-300,ev.clientX));document.documentElement.style.setProperty('--width',w+'px')};r.onpointerup=()=>{r.onpointermove=null;try{localStorage.setItem('mdportal-width',parseInt(getComputedStyle(document.documentElement).getPropertyValue('--width')))}catch(e){}}});
content.addEventListener('click',async e=>{const toggle=e.target.closest('.toggle-source');if(toggle){const pre=toggle.closest('.codeblock').querySelector('pre');pre.hidden=!pre.hidden;return}const b=e.target.closest('.copy');if(!b)return;const text=b.closest('.codeblock').querySelector('pre').textContent;try{await navigator.clipboard.writeText(text)}catch(e){const t=el('textarea');t.value=text;t.style.position='fixed';t.style.opacity='0';document.body.append(t);t.select();const ok=document.execCommand('copy');t.remove();if(!ok){b.textContent='Non copiato';return}}b.textContent='Copiato';setTimeout(()=>b.textContent='Copia',1200)});
</script></body></html>'''

if __name__=='__main__': main()
