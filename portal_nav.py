"""Deterministic, project-independent navigation, with monorepo conventions."""
import re
from pathlib import PurePosixPath

def natural(s):
    return tuple((0,int(x)) if x.isdigit() else (1,x.casefold()) for x in re.split(r'(\d+)',s))

def pretty(s):
    return s.replace('_',' ').replace('-',' ').strip().title() if not s.isupper() else s

def is_index(p):
    return PurePosixPath(p['path']).stem.lower() in ('readme','index')

def leaf(p,label=None):
    m=p.get('meta',{}); n={'label':label or p['title'],'page':p['path']}
    if m.get('type')=='feature': n.update(id=m.get('id'),status=m.get('status'))
    return n

def sort_doc(p):
    stem=PurePosixPath(p['path']).stem.lower()
    tail={'roadmap':1,'api-rest':2,'sdk-grpc':3,'ruoli-permessi':4,'changelog':5}
    return (0 if is_index(p) else 1,tail.get(stem,0),natural(p.get('meta',{}).get('id') or p['path']))

def group(label,items,prefix=0,open_=False,collapse_doc=False):
    """Promote directory README to a clickable branch; no duplicate leaf."""
    # recurse on logical relative paths, without altering canonical links
    return logical_group(label,[(p,p['path'].split('/')[prefix:][1:] if collapse_doc and p['path'].split('/')[prefix:][0].lower() in ('doc','docs') and len(p['path'].split('/')[prefix:])>1 else p['path'].split('/')[prefix:]) for p in items],open_)

def logical_group(label,items,open_=False):
    direct=[p for p,parts in items if len(parts)==1]; dirs={}
    for p,parts in items:
        if len(parts)>1: dirs.setdefault(parts[0],[]).append((p,parts[1:]))
    # Prefer the actual root index when doc/README is also flattened.
    index=next(iter(sorted((p for p in direct if is_index(p)),key=lambda p:(len(p['path'].split('/')),sort_doc(p)))),None)
    node=leaf(index,label) if index else {'label':label}
    children=[logical_group(pretty(k),dirs[k]) for k in sorted(dirs,key=natural)]
    children.extend(leaf(p) for p in sorted(direct,key=sort_doc) if p is not index)
    if children: node.update(children=children,open=open_)
    return node

def service_group(name,items):
    label=pretty(re.sub(r'^(?:ilm-admin|afc|core|net|ui)-','',name,flags=re.I))
    if not any('/doc/features/' in p['path'] or '/docs/features/' in p['path'] for p in items):
        return group(label,items,2,collapse_doc=True)
    root=[p for p in items if len(p['path'].split('/'))==3 and is_index(p)]
    node=leaf(root[0],label) if root else {'label':label}
    buckets={k:[] for k in ('Funzionalità','Attori','API','Tecnica','Altro','Da collocare')}
    for p in items:
        if any(p is r for r in root[:1]): continue
        parts=p['path'].split('/')[2:]
        if parts[0] in ('doc','docs') and len(parts)>1: parts=parts[1:]
        k=parts[0].lower(); stem=PurePosixPath(parts[-1]).stem.lower()
        bucket={'features':'Funzionalità','apis':'API','technical':'Tecnica','extra':'Da collocare'}.get(k)
        if not bucket: bucket='Funzionalità' if stem=='roadmap' else 'Attori' if stem=='attori' else 'Altro'
        if k in ('features','apis','technical','extra') and len(parts)>1: parts=parts[1:]
        buckets[bucket].append((p,parts))
    children=[]
    for k,rows in buckets.items():
        if not rows: continue
        if k=='Attori' and len(rows)==1: children.append(leaf(rows[0][0],'Attori'))
        elif k=='Funzionalità':
            children.append({'label':k,'children':[leaf(p) for p,_ in sorted(rows,key=lambda r:(PurePosixPath(r[0]['path']).stem.lower()!='roadmap',natural(r[0].get('meta',{}).get('id') or r[0]['path']))) ]})
        else: children.append(logical_group(k,rows))
    if children: node['children']=children
    return node

def build_navigation(pages):
    """Return section/branch/leaf model, covering every page exactly once."""
    roots=[]; folders={}; dashboard=[]
    for p in pages:
        if p.get('dashboard'): dashboard.append(leaf(p)); continue
        parts=p['path'].split('/')
        if len(parts)==1: roots.append(p)
        else: folders.setdefault(parts[0],[]).append(p)
    sections=[]
    def add(label,children):
        if children: sections.append({'label':label,'children':children})
    add('Main',[leaf(p) for p in sorted(roots,key=sort_doc)])
    ecosystem=[]
    for key in ('doc','docs'):
        if key in folders:
            n=group('Documentazione globale',folders.pop(key),1)
            if n.get('page'): ecosystem.append({k:v for k,v in n.items() if k not in ('children','open')})
            ecosystem.extend(n.get('children',[]))
    # Keep introductory architecture/stack documents before nested reference material.
    ecosystem.sort(key=lambda n: (0 if n.get('page') in ('doc/stack.md','docs/stack.md') else 1 if n.get('page') in ('doc/README.md','docs/README.md') else 2))
    add('Ecosistema' if ecosystem else 'Progetto',ecosystem+dashboard)
    if 'apps' in folders:
        services={}; direct=[]
        for p in folders.pop('apps'):
            parts=p['path'].split('/')
            if len(parts)<3: direct.append(leaf(p)); continue
            services.setdefault(parts[1],[]).append(p)
        groups={}
        for name,items in sorted(services.items(),key=lambda x:natural(x[0])):
            prefix=next((label for pre,label in [('afc-','AFC'),('core-','CORE'),('ilm-','ILM'),('net-','NET'),('ui-','UI')] if name.startswith(pre)),None)
            groups.setdefault(prefix or 'Altri',[]).append(service_group(name,items))
        add('Servizi',[{'label':k,'children':groups[k],'open':True} for k in ('AFC','CORE','ILM','NET','UI','Altri') if k in groups]+direct)
    for key in ('deploy','_packages'):
        if key in folders:
            n=group('Overview',folders.pop(key),1); add('Deploy' if key=='deploy' else 'Packages',([ {k:v for k,v in n.items() if k not in ('children','open')} ] if n.get('page') else [])+n.get('children',[]))
    for name,items in sorted(folders.items(),key=lambda x:natural(x[0])):
        n=group(pretty(name),items,1)
        add(pretty(name),([{k:v for k,v in n.items() if k not in ('children','open')}] if n.get('page') else [])+n.get('children',[]))
    return sections

def navigation_order(sections):
    def walk(n):
        if n.get('page'): yield n['page']
        for c in n.get('children',[]): yield from walk(c)
    return [p for n in sections for p in walk(n)]
