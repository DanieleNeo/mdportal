"""Lossless resource registry, CSV previews, and size optimization for MD Portal."""
import base64, csv, gzip, hashlib, html, io, json, mimetypes, re
from pathlib import Path

DATA_EXTS=set('.csv .tsv .json .jsonl .ndjson .geojson .xml .yaml .yml .txt .log .dat .parquet .xlsx .xls .ods .pdf .zip .gz .7z .bin .png .jpg .jpeg .svg .webp .gif .bmp .tif .tiff .flac .mp3 .wav .mp4 .sql'.split())

class Assets:
    def __init__(self, root, ignore, md_exts, excluded=()):
        self.root=root; self.ignore=ignore; self.md_exts=md_exts; self.excluded=set(excluded)
        self.paths={}; self.entries={}; self.by_digest={}; self._index=None
        self.implicit_dirs=0; self.implicit_files=0; self.ambiguous=[]
    def allowed(self,p):
        return p.is_relative_to(self.root) and p not in self.excluded and not any(x in self.ignore or x.startswith('.') for x in p.relative_to(self.root).parts)
    def files_in(self,p):
        return sorted({q.resolve() for q in p.rglob('*') if q.is_file() and self.allowed(q.resolve()) and q.suffix.lower() not in self.md_exts}, key=lambda q:q.as_posix().casefold())
    def resolve_mention(self, text, source):
        # Only actual inline code tokens: never scan fenced code, shell commands or prose.
        if not text or len(text)>350 or any(c in text for c in '\r\n\x00<>|*?') or re.match(r'^[a-zA-Z]+:',text): return []
        text=text.replace('\\','/')
        if text.startswith('/') or text.startswith('~'): return []
        if text.endswith('/'):
            for base in (source.parent,self.root):
                p=(base/text).resolve()
                if p!=self.root and self.allowed(p) and p.is_dir():
                    self.implicit_dirs+=1; return self.files_in(p)
            return []
        if Path(text).suffix.lower() not in DATA_EXTS: return []
        for base in (source.parent,self.root):
            p=(base/text).resolve()
            if self.allowed(p) and p.is_file(): return [p]
        if '/' in text: return []
        if self._index is None:
            self._index={}
            for p in self.files_in(self.root):
                self._index.setdefault(p.name.casefold(),[]).append(p)
        matches=self._index.get(text.casefold(),[])
        if len(matches)>1: self.ambiguous.append((str(source.relative_to(self.root)),text))
        return matches if len(matches)==1 else []
    def register(self,p):
        if p not in self.paths:
            data=p.read_bytes(); mime=mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
            key=(hashlib.sha256(data).hexdigest(),mime)
            aid=self.by_digest.get(key)
            if aid is None:
                aid=str(len(self.entries)); self.by_digest[key]=aid
                self.entries[aid]={'mime':mime,'data':base64.b64encode(data).decode('ascii'),'size':len(data),'sha256':key[0]}
                if p.suffix.lower() in ('.csv','.tsv'): self.entries[aid]['preview']=csv_preview(data,p.suffix.lower())
            self.paths[p]=aid
        return self.paths[p]
    def attach(self,tag,p):
        tag['href']='#'; tag['data-asset']=self.register(p); tag['download']=p.name; tag['title']='Scarica '+p.name
    def image(self,tag,p):
        tag['data-image']=self.register(p); del tag['src']; tag['loading']='lazy'
    def catalog(self,soup,files):
        if not files: return
        section=soup.new_tag('section',attrs={'class':'asset-catalog','id':'section-allegati-incorporati'})
        title=soup.new_tag('h2'); title.string=f'Allegati incorporati ({len(files)})'; section.append(title)
        note=soup.new_tag('p'); note.string='File originali disponibili offline. L’anteprima CSV non modifica i dati scaricabili.'; section.append(note)
        ul=soup.new_tag('ul'); section.append(ul)
        for p in sorted(files,key=lambda p:p.relative_to(self.root).as_posix().casefold()):
            li=soup.new_tag('li'); a=soup.new_tag('a'); a.string=p.relative_to(self.root).as_posix(); self.attach(a,p); li.append(a)
            aid=self.paths[p]; entry=self.entries[aid]; small=soup.new_tag('small'); small.string=f' · {entry["size"]:,} byte'; li.append(small)
            if entry.get('preview'):
                b=soup.new_tag('button',attrs={'type':'button','class':'asset-preview','data-preview':aid,'data-name':p.name}); b.string='Anteprima'; li.append(b)
            ul.append(li)
        soup.append(section)


def csv_preview(data,suffix):
    # Never modify the source file; limit preview size, not the original download.
    if len(data)>20*1024*1024: return None
    try:
        if data.startswith((b'\xff\xfe',b'\xfe\xff')): text=data.decode('utf-16')
        else:
            try: text=data.decode('utf-8-sig')
            except UnicodeDecodeError: text=data.decode('cp1252')
        if '\x00' in text: return None
        try: dialect=csv.Sniffer().sniff(text[:16384],delimiters=',;\t|'); delim=dialect.delimiter
        except csv.Error: delim='\t' if suffix=='.tsv' else max([',',';','\t','|'],key=lambda c:text.splitlines()[0].count(c) if text else 0)
        reader=csv.reader(io.StringIO(text,newline=''),delimiter=delim); rows=[]; total=0
        for row in reader:
            total+=1
            if len(rows)<201: rows.append([v[:2000]+('…' if len(v)>2000 else '') for v in row[:100]])
        return {'rows':rows,'total':total,'delimiter':delim} if rows else None
    except (UnicodeError,csv.Error,ValueError): return None


def compact_markup(soup):
    # Remove indentation BETWEEN blocks only; keep inline spaces and all pre/code text.
    from bs4 import NavigableString, Tag
    blocks={'p','div','section','h1','h2','h3','h4','h5','h6','ul','ol','li','table','thead','tbody','tr','td','th','blockquote','pre','hr'}
    for node in list(soup.find_all(string=True)):
        if type(node) is not NavigableString or str(node).strip() or '\n' not in node or node.find_parent(['pre','code','textarea','script','style']): continue
        prev,nxt=node.previous_sibling,node.next_sibling
        if (prev is None or isinstance(prev,Tag) and prev.name in blocks) and (nxt is None or isinstance(nxt,Tag) and nxt.name in blocks): node.extract()
    return str(soup)


def minify_shell(shell):
    stats={}; engines={}
    def css(m):
        src=m[1]
        try:
            import rcssmin
            dst=rcssmin.cssmin(src,keep_bang_comments=True); engines['css']='rcssmin'
        except ImportError:
            dst='\n'.join(s.strip() for s in src.splitlines() if s.strip()); engines['css']='conservative whitespace'
        stats['css_before']=len(src.encode()); stats['css_after']=len(dst.encode()); return '<style>'+dst+'</style>'
    def js(m):
        src=m[1]
        try:
            import rjsmin
            dst=rjsmin.jsmin(src,keep_bang_comments=True); engines['js']='rjsmin'
        except ImportError:
            # Keep line boundaries (ASI) and never rewrite regexes or string literals.
            dst='\n'.join(src.splitlines()).strip(); engines['js']='conservative whitespace'
        stats['js_before']=stats.get('js_before',0)+len(src.encode()); stats['js_after']=stats.get('js_after',0)+len(dst.encode()); return '<script>'+dst+'</script>'
    shell=re.sub(r'<style>(.*?)</style>',css,shell,flags=re.S)
    shell=re.sub(r'<script>(.*?)</script>',js,shell,flags=re.S)
    # Shell is authored static markup; do not apply this operation to user content.
    shell=re.sub(r'(?<=>)\n(?=<(?:/?(?:html|head|body|header|aside|main|script|style|div|article|template)\b))','',shell)
    return shell,stats,engines


def gzip_html(document,title):
    raw=document.encode('utf-8'); gz=gzip.compress(raw,compresslevel=9,mtime=0); b64=base64.b64encode(gz).decode('ascii')
    # All resources are embedded. The bootstrap needs no network or external JS.
    # document.write is deliberately used on a NEW document to execute the existing portal scripts.
    wrapper='''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+html.escape(title)+'''</title></head><body style="font:16px system-ui;padding:2rem"><p id="loading">Apertura del portale offline…</p><script id="payload" type="application/octet-stream">'''+b64+'''</script><script>(async()=>{try{if(typeof DecompressionStream!=="function")throw Error("Browser senza supporto alla decompressione gzip: usare Chrome, Edge o Firefox aggiornato, oppure rigenerare con --compression none.");const b=Uint8Array.from(atob(document.getElementById("payload").textContent),c=>c.charCodeAt(0));const h=await new Response(new Blob([b]).stream().pipeThrough(new DecompressionStream("gzip"))).text();document.open();document.write(h);document.close()}catch(e){document.getElementById("loading").textContent="Impossibile aprire il portale: "+e.message}})()</script></body></html>'''
    return wrapper,len(gz)
