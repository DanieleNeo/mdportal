"""Build-time SVG rendering with local, network-isolated Chromium and bundled engines."""
from pathlib import Path
import time
from bs4 import BeautifulSoup
from portal_browser import launch_browser

VENDOR=Path(__file__).resolve().parent/'vendor'


def prerender(soups, warn, browser_path=None):
    jobs=[]
    for f,soup in soups.items():
        for item in soup.select('.math-tex'):
            jobs.append((f,item,'math',item.get_text(),item.get('data-display')=='true'))
        for item in soup.select('.mermaid-block'):
            jobs.append((f,item,'mermaid',item.select_one('pre').get_text(),True))
    stats={k:{'count':0,'rendered':0,'failed':0,'svg_bytes':0,'library_bytes':0} for k in ('math','mermaid')}
    if not jobs:return stats
    for _,_,kind,_,_ in jobs:stats[kind]['count']+=1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError('Rendering statico: installare playwright (pip install -r requirements.txt; py -m playwright install chromium), oppure usare --render-mode runtime.') from e
    t=time.perf_counter()
    with sync_playwright() as p:
        browser, stats['browser'] = launch_browser(p.chromium, browser_path)
        try:
            context=browser.new_context(viewport={'width':1280,'height':900},offline=True,service_workers='block')
            context.route('**/*',lambda route:route.abort())
            page=context.new_page()
            page.set_default_timeout(30000)
            page.set_content('<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;font-family:Arial,sans-serif;font-size:16px"></body></html>')
            if stats['math']['count']:
                bundle=VENDOR/'mathjax-tex-svg.js';stats['math']['library_bytes']=bundle.stat().st_size
                page.evaluate("""() => {window.MathJax={startup:{typeset:false},tex:{packages:['base','ams','newcommand','textmacros'],maxBuffer:100000},svg:{fontCache:'none'},options:{enableMenu:false}}}""")
                page.add_script_tag(path=str(bundle)); page.evaluate('() => MathJax.startup.promise')
            if stats['mermaid']['count']:
                bundle=VENDOR/'mermaid.bundle.js';stats['mermaid']['library_bytes']=bundle.stat().st_size
                page.add_script_tag(path=str(bundle))
            for i,(f,item,kind,source,display) in enumerate(jobs,1):
                try:
                    if kind=='math':
                        markup=page.evaluate("""async ({source,display})=>{const node=await MathJax.tex2svgPromise(source,{display});if(node.querySelector('[data-mml-node="merror"]'))throw Error('Formula LaTeX non valida');const svg=node.querySelector('svg');if(!svg)throw Error('SVG mancante');return svg.outerHTML;}""",{'source':source,'display':display})
                        # Keep TeX as accessible text and machine-readable source, without runtime renderer.
                        item.clear();item.append(BeautifulSoup(markup,'html.parser'))
                        item['aria-label']=source;item['data-tex']=source;item['data-math-rendered']='true'
                        item['role']='math';item['class']=item.get('class',[])+['math-static']
                        size=len(markup.encode())
                    else:
                        variants=page.evaluate("""async ({source,i})=>{const out=[];for(const theme of ['default','dark']){mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme,suppressErrorRendering:true,deterministicIds:true,deterministicIDSeed:'mdp-'+i+'-'+theme});const r=await mermaid.render('mdp-static-'+i+'-'+theme,source);if(!r.svg)throw Error('SVG mancante');out.push(r.svg)}return out;}""",{'source':source,'i':i})
                        view=item.select_one('.mermaid-view');view.clear()
                        size=0
                        for theme,markup in zip(('light','dark'),variants):
                            svg=BeautifulSoup(markup,'html.parser').find('svg')
                            if svg is None:raise ValueError('SVG mancante')
                            svg['class']=svg.get('class',[])+['diagram-'+theme];view.append(svg);size+=len(markup.encode())
                        item['class']=item.get('class',[])+['mermaid-static']
                    stats[kind]['rendered']+=1;stats[kind]['svg_bytes']+=size
                except Exception as e:
                    stats[kind]['failed']+=1
                    warn(f,f'Rendering SVG {kind}: {str(e).splitlines()[0]}')
                    if kind=='math':
                        item.clear();item.string=source;item['data-math-error']='true';item['class']=item.get('class',[])+['math-static']
                    else:
                        view=item.select_one('.mermaid-view');view.clear();view.string='Diagramma non renderizzabile';item.select_one('pre').attrs.pop('hidden',None);item['class']=item.get('class',[])+['mermaid-static']
                if i%25==0 or i==len(jobs):print(f'  Rendering SVG: {i}/{len(jobs)}',flush=True)
        finally:browser.close()
    stats['seconds']=round(time.perf_counter()-t,3)
    return stats
