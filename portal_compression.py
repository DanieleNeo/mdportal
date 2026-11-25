"""Lossless self-extracting HTML. Select by actual UTF-8 output bytes, not payload size."""
import base64, gzip, html, json, lzma, shutil, subprocess, time, zlib
from pathlib import Path

VENDOR=Path(__file__).resolve().parent/'vendor'
ALPH91=''.join(chr(i) for i in range(33,127) if chr(i) not in '<>\\')
ALPH85=ALPH91[:85]
RFC85='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!#$%&()*+-;<=>?@^_`{|}~'
TRAN85=str.maketrans(RFC85,ALPH85)
ALGORITHMS=('gzip','deflate','zopfli','brotli','lzma','zstd')
ENCODINGS=('base64','base85','base91')
JS64='const b=Uint8Array.from(atob(s),c=>c.charCodeAt(0));'
JS85='''const a=ALPH, m=Object.fromEntries([...a].map((c,i)=>[c,i])),b=new Uint8Array(Math.floor(s.length*4/5));let p=0;for(let i=0;i<s.length;i+=5){let n=Math.min(5,s.length-i),v=0;for(let j=0;j<5;j++)v=v*85+(j<n?m[s[i+j]]:84);for(let j=0;j<n-1;j++)b[p++]=(v/2**(24-8*j))&255;}'''.replace('ALPH',json.dumps(ALPH85))
JS91='''const a=ALPH,m=Object.fromEntries([...a].map((c,i)=>[c,i])),o=new Uint8Array(Math.ceil(s.length*7/8));let p=0,v=-1,q=0,n=0;for(const c of s){const d=m[c];if(v<0)v=d;else{v+=d*91;q|=v<<n;n+=(v&8191)>88?13:14;while(n>=8){o[p++]=q&255;q>>>=8;n-=8}v=-1}}if(v>=0)o[p++]=(q|v<<n)&255;const b=o.subarray(0,p);'''.replace('ALPH',json.dumps(ALPH91))

def encode(data,mode):
    if mode=='base64':return base64.b64encode(data).decode('ascii')
    if mode=='base85':return base64.b85encode(data).decode('ascii').translate(TRAN85)
    q=n=0; out=bytearray(); a=ALPH91.encode('ascii')
    for x in data:
        q|=x<<n;n+=8
        if n>13:
            v=q&8191
            if v>88:q>>=13;n-=13
            else:v=q&16383;q>>=14;n-=14
            out.extend((a[v%91],a[v//91]))
    if n:
        out.append(a[q%91])
        if n>7 or q>90:out.append(a[q//91])
    return out.decode('ascii')

def compress(raw,algo,iters):
    if algo=='gzip':return gzip.compress(raw,compresslevel=9,mtime=0),'9'
    if algo=='deflate':
        c=zlib.compressobj(9,wbits=-15);return c.compress(raw)+c.flush(),'9 (raw)'
    if algo=='zopfli':
        import zopfli.gzip
        return zopfli.gzip.compress(raw,numiterations=iters),f'{iters} iterazioni'
    if algo=='brotli':
        import brotli
        return brotli.compress(raw,quality=11,mode=brotli.MODE_TEXT),'11'
    if algo=='lzma':return lzma.compress(raw,format=lzma.FORMAT_RAW,filters=[{'id':lzma.FILTER_LZMA1,'preset':9,'lc':3,'lp':0,'pb':2}]),'9 (LZMA1 raw)'
    if algo=='zstd':
        try:
            import zstandard
            return zstandard.ZstdCompressor(level=22).compress(raw),'22'
        except ImportError:
            if not shutil.which('zstd'):raise ImportError('installare zstandard o zstd CLI')
            return subprocess.run(['zstd','-q','--ultra','-22','-c'],input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout,'22 (CLI)'
    raise ValueError(algo)

def decoder(algo,zstd_path):
    if algo in ('gzip','zopfli','deflate'):
        fmt='deflate-raw' if algo=='deflate' else 'gzip'
        return '',f'const d=new Uint8Array(await new Response(new Blob([b]).stream().pipeThrough(new DecompressionStream({json.dumps(fmt)}))).arrayBuffer());'
    if algo=='brotli':return (VENDOR/'brotli-decoder.js').read_text(encoding='utf-8'),'const d=mdBrotli(b);'
    if algo=='lzma':return (VENDOR/'lzma-decoder.js').read_text(encoding='utf-8'),'const d=mdLzma(b,SIZE);'
    if not zstd_path:raise ValueError('decoder Zstd non disponibile: fornire --zstd-decoder con fzstd UMD')
    src=Path(zstd_path).read_text(encoding='utf-8')
    return src,'const d=globalThis.fzstd.decompress(b);'

def wrapper(payload,algo,encoding,title,raw_len,src,decode):
    # The raw-text payload cannot contain '<', so no HTML tag can terminate it.
    # Reject an unsafe decoder rather than mutate JS source or silently break it.
    if '<' in payload or '</script' in src.lower():raise ValueError('payload o decoder non sicuro per script HTML')
    js={'base64':JS64,'base85':JS85,'base91':JS91}[encoding]
    return ('<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title></head><body style="font:16px system-ui;padding:2rem"><p id="loading">Apertura del portale offline…</p><script id="payload" type="application/octet-stream">'+payload+'</script><script>'+src+'\n(async()=>{try{await new Promise(r=>setTimeout(r,0));const s=document.getElementById("payload").textContent;'+js+decode.replace('SIZE',str(raw_len))+'if(d.length!=='+str(raw_len)+')throw Error("dimensione non valida");const h=new TextDecoder("utf-8",{fatal:true}).decode(d);document.open();document.write(h);document.close()}catch(e){document.getElementById("loading").textContent="Impossibile aprire il portale: "+e.message+". Rigenerare con --profile compatible oppure --compression none."}})()</script></body></html>')

def optimize(document,title,algo='auto',profile='maximum',encoding='auto',iters=5,zstd_path=None):
    raw=document.encode('utf-8'); best=document if algo in ('auto','none') else None; rows=[]; selected={'algorithm':'none','encoding':'none','final_html_bytes':len(raw),'payload_bytes':None}; gz_size=None
    if algo=='none':return document,selected,rows,gz_size
    algos=ALGORITHMS if algo=='auto' else (algo,)
    for a in algos:
        if profile=='compatible' and a not in ('gzip','deflate','zopfli'):
            rows.append({'algorithm':a,'status':'skipped','reason':'profilo compatible'});continue
        try:
            t=time.perf_counter(); data,level=compress(raw,a,iters); sec=time.perf_counter()-t
            if a=='gzip':gz_size=len(data)
            try:src,decode=decoder(a,zstd_path)
            except (ValueError,OSError) as e:
                rows.append({'algorithm':a,'status':'skipped','reason':str(e),'level':level,'payload_bytes':len(data),'compression_seconds':round(sec,4),'payload_ratio':round(len(raw)/len(data),4)});print(f'  {a:<9} {len(data):>12,} B payload | escluso: {e}',flush=True);continue
            for enc in (ENCODINGS if encoding=='auto' else (encoding,)):
                t=time.perf_counter(); text=encode(data,enc); packed=wrapper(text,a,enc,title,len(raw),src,decode); size=len(packed.encode('utf-8')); encsec=time.perf_counter()-t
                row={'algorithm':a,'level':level,'encoding':enc,'status':'ok','payload_bytes':len(data),'encoded_bytes':len(text),'decoder_bytes':len(src.encode('utf-8')),'wrapper_overhead_bytes':size-len(text),'final_html_bytes':size,'payload_ratio':round(len(raw)/len(data),4),'final_ratio':round(len(raw)/size,4),'reduction_percent':round(100*(1-size/len(raw)),4),'compression_seconds':round(sec,4),'encoding_seconds':round(encsec,4)};rows.append(row)
                print(f'  {a:<9} {enc:<7} {len(data):>10,} B -> {size:>10,} B | {row["reduction_percent"]:6.2f}% | {sec:.2f}s + {encsec:.2f}s',flush=True)
                if best is None or size<len(best.encode('utf-8')):best=packed;selected=row.copy()
        except (ImportError,OSError,ValueError,subprocess.SubprocessError) as e:
            rows.append({'algorithm':a,'status':'skipped','reason':str(e)});print(f'  {a}: escluso ({e})',flush=True)
    if best is None:raise RuntimeError(f'Compressione {algo} non disponibile nel profilo {profile}; vedere i motivi sopra.')
    print(f'  Selezione: {selected["algorithm"]} / {selected["encoding"]} ({selected["final_html_bytes"]:,} byte)',flush=True)
    return best,selected,rows,gz_size
