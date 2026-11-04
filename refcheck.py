#!/usr/bin/env python3
"""Local IEEE-style reference intake, metadata check, SQLite catalog, and query UI."""
import argparse, csv, datetime as dt, html, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'references.sqlite3'

def connect():
    DB.parent.mkdir(exist_ok=True)
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS refs(id INTEGER PRIMARY KEY, citation TEXT NOT NULL, title TEXT, authors TEXT, venue TEXT, year TEXT, kind TEXT, source_file TEXT NOT NULL, source_number TEXT, online_status TEXT, matched_title TEXT, doi TEXT, url TEXT, format_issues TEXT, raw_metadata TEXT, batch_id TEXT, created_at TEXT, UNIQUE(citation,source_file,source_number))''')
    c.execute('CREATE INDEX IF NOT EXISTS refs_title ON refs(title)'); c.execute('CREATE INDEX IF NOT EXISTS refs_authors ON refs(authors)'); c.commit(); return c

def extract_text(path):
    ext=path.suffix.lower()
    if ext=='.txt': return path.read_text(errors='replace')
    if ext in {'.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'}:
        exe=shutil.which('tesseract')
        if not exe: raise RuntimeError('OCR image input requires Tesseract (`brew install tesseract`).')
        # Two-column reference pages are read much more reliably one column at a time.
        from PIL import Image
        with Image.open(path) as im:
            w,h=im.size; mid=w//2
            columns=[im.crop((0,0,mid,h)),im.crop((mid,0,w,h))]
            outputs=[]
            for col in columns:
                tmp=ROOT/'work'; tmp.mkdir(exist_ok=True); name=tmp/'ocr-column.png'; col.save(name)
                p=subprocess.run([exe,str(name),'stdout','--psm','4'],capture_output=True,text=True)
                if p.returncode: raise RuntimeError(p.stderr.strip() or 'Tesseract OCR failed')
                outputs.append(p.stdout)
            return '\n'.join(outputs)
    raise ValueError(f'Unsupported input extension {ext}; use .txt or a common image format.')

def split_refs(text):
    # Reference numbering such as [1] starts a new entry; wrapped lines stay together.
    chunks=[]; current=[]; number=''
    for line in text.replace('\r','').splitlines():
        m=re.match(r'^\s*\[(\d+)\]\s*(.*)',line)
        if m:
            if current: chunks.append((number,' '.join(x.strip() for x in current if x.strip())))
            number=m.group(1); current=[m.group(2)]
        elif current: current.append(line)
    if current: chunks.append((number,' '.join(x.strip() for x in current if x.strip())))
    return chunks

def infer(c):
    title_match=re.search(r'[“\"](.*?)[”\"]',c)
    if not title_match: title_match=re.search(r'“(.*?)”',c)
    title=re.sub(r'-\s+', '', title_match.group(1)).strip(' ,') if title_match else ''
    authors=(c[:title_match.start()].strip(' ,') if title_match else c.split(',',1)[0].strip())
    year=(re.findall(r'\b(?:19|20)\d{2}\b',c) or [''])[0]
    venue=''; kind='Unclassified'
    low=c.lower()
    if 'arxiv.org' in low or 'corr' in low or 'preprint' in low: kind='Preprint'; venue='arXiv / preprint'
    elif re.search(r'\bin\s+(?:\d{4}\s+)?(?:\d+(?:st|nd|rd|th)?\s+)?(?:international|annual|acm|ieee|conference|symposium|workshop|neurips|esscirc|design automation|advances in neural)',low): kind='Conference paper'
    elif re.search(r'\b(journal|trans-?\s*actions|letters|review|evolution\w*|computing)\b',low): kind='Journal paper'
    elif re.search(r'https?://',low) and re.search(r'development team|software|simulator|library|documentation|website',low): kind='Website / software'
    elif re.search(r'https?://',low) and not title: kind='Website / software'
    if kind=='Journal paper':
        m=re.search(r'([A-Z][^,\"]{3,}?(?:Journal|Transactions|Letters|Evolution|Computation)[^,\"]*)',c,re.I); venue=m.group(1).strip() if m else ''
    elif kind=='Conference paper':
        m=re.search(r'\bin\s+(.+?)(?:,\s*(?:vol\.|20\d\d|pp\.|pages)|$)',c,re.I); venue=m.group(1).strip() if m else ''
    elif kind in {'Preprint','Website / software'}: venue='arXiv' if 'arxiv' in low or 'corr' in low else ''
    issues=[]
    if not title: issues.append('Title not detected in quotation marks; check transcription and title field.')
    if not year: issues.append('Publication year is missing.')
    if kind=='Journal paper':
        for field,pat in [('journal volume',r'\bvol\.'),('page range or article number',r'\b(?:pp?\.|pages?)\b|\bp\.\s*\d')]:
            if not re.search(pat,c,re.I): issues.append(f'Missing {field}.')
        if not re.search(r'\bno\.',c,re.I): issues.append('Issue number not stated (include if assigned/available).')
    if kind=='Conference paper':
        if not re.search(r'\b(?:pp?\.|pages?)\b',c,re.I): issues.append('Conference page range is missing.')
        if not venue: issues.append('Conference/proceedings name not detected.')
    if kind=='Preprint' and not re.search(r'arxiv|doi|url',low): issues.append('Preprint repository or identifier is missing.')
    if 'http://' in low: issues.append('URL uses HTTP; use HTTPS when available.')
    doi=(re.search(r'10\.\d{4,9}/[-._;()/:A-Z0-9]+',c,re.I) or [None,''])[1].rstrip('.,')
    url_match=re.search(r'https?://\S+',c); url=url_match.group(0).rstrip('.,') if url_match else ''
    # author list is retained as a compact parse hint; exact author parsing is intentionally conservative.
    return dict(title=title,authors=authors,venue=venue,year=year,kind=kind,issues=issues,doi=doi,url=url)

def crossref(meta):
    if not meta['title']: return 'Not checked: no title detected.',None
    q=urllib.parse.urlencode({'query.bibliographic':meta['title'],'rows':'1','select':'title,author,container-title,published,DOI,type,URL'})
    req=urllib.request.Request('https://api.crossref.org/works?'+q,headers={'User-Agent':'RefCheckLocal/1.0 (offline-first reference tool)'})
    try:
        # Crossref's public endpoint is rate limited; cache repeated titles within a batch.
        time.sleep(1.05)
        with urllib.request.urlopen(req,timeout=8) as r: data=json.load(r)
        items=data.get('message',{}).get('items',[])
        if not items: return 'No Crossref match found (not proof the work is nonexistent).',None
        item=items[0]; match=(item.get('title') or [''])[0]
        from difflib import SequenceMatcher
        score=SequenceMatcher(None,re.sub(r'\W','',meta['title'].lower()),re.sub(r'\W','',match.lower())).ratio()
        if score<.65: return f'Possible but weak Crossref match ({score:.0%} title similarity); review manually.',item
        return f'Likely Crossref record ({score:.0%} title similarity); verify edition and fields.',item
    except Exception as e: return f'Online check unavailable: {type(e).__name__}: {e}',None

def process(paths,check_online=True,publish=True):
    c=connect(); batch=dt.datetime.now().astimezone().strftime('%Y%m%d-%H%M%S'); rows=[]; online_cache={}
    for path in paths:
        p=Path(path).expanduser().resolve()
        if not p.is_file(): print(f'SKIP missing: {p}',file=sys.stderr); continue
        text=extract_text(p)
        refs=split_refs(text)
        for number,citation in refs:
            meta=infer(citation); status='Online check disabled.'; item=None
            cache_key=re.sub(r'\W','',meta['title'].lower())
            if check_online:
                if cache_key not in online_cache: online_cache[cache_key]=crossref(meta)
                status,item=online_cache[cache_key]
            if item:
                meta['doi']=meta['doi'] or item.get('DOI',''); meta['url']=meta['url'] or item.get('URL','')
            c.execute('''INSERT INTO refs(citation,title,authors,venue,year,kind,source_file,source_number,online_status,matched_title,doi,url,format_issues,raw_metadata,batch_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(citation,source_file,source_number) DO UPDATE SET batch_id=excluded.batch_id, online_status=excluded.online_status, format_issues=excluded.format_issues''', (citation,meta['title'],meta['authors'],meta['venue'],meta['year'],meta['kind'],p.name,number,status,(item.get('title') or [''])[0] if item else '',meta['doi'],meta['url'],json.dumps(meta['issues']),json.dumps(item) if item else '',batch,dt.datetime.now().astimezone().isoformat()))
            rows.append({'source':p.name,'number':number,'citation':citation,**meta,'online_status':status,'matched_title':(item.get('title') or [''])[0] if item else ''})
    c.commit()
    snap=ROOT/'snapshots'/f'references-{batch}.sqlite3'; snap.parent.mkdir(exist_ok=True)
    dest=sqlite3.connect(snap); c.backup(dest); dest.close(); c.close()
    (ROOT/'reports').mkdir(exist_ok=True)
    md=['# Reference check report',f'Batch: `{batch}`',f'Processed entries: {len(rows)}','']
    for r in rows:
        md += [f"## [{r['number']}] {r['title'] or '(title not parsed)'}",f"- Source: `{r['source']}`",f"- Type: {r['kind']} | Year: {r['year'] or 'unknown'} | Venue: {r['venue'] or 'unknown'}",f"- Online: {r['online_status']}"]
        if r['matched_title']: md.append(f"- Crossref title: {r['matched_title']}")
        md.append('- IEEE-style review: '+('; '.join(r['issues']) if r['issues'] else 'No obvious missing core fields detected; verify punctuation/order and source-specific requirements.'))
        md.append('')
    report=ROOT/'reports'/f'report-{batch}.md'; report.write_text('\n'.join(md))
    (ROOT/'reports'/'latest.json').write_text(json.dumps({'batch_id':batch,'snapshot':str(snap.relative_to(ROOT)),'report':str(report.relative_to(ROOT)),'count':len(rows)},indent=2))
    print(f'Added/updated {len(rows)} references. Snapshot: {snap}\nReport: {report}')
    if publish: publish_batch(batch)

def publish_batch(batch):
    def git(*args):
        return subprocess.run(['git','-C',str(ROOT),*args],capture_output=True,text=True)
    if git('rev-parse','--is-inside-work-tree').returncode:
        print('Git publish skipped: initialize this folder as a Git repository and configure origin.')
        return
    if git('remote','get-url','origin').returncode:
        print('Git publish skipped: no origin remote is configured.')
        return
    added=git('add','--','data/references.sqlite3','reports','snapshots')
    if added.returncode: raise RuntimeError('Could not stage updated catalog: '+added.stderr.strip())
    committed=git('commit','-m',f'Update reference catalog {batch}')
    if committed.returncode: raise RuntimeError('Could not commit catalog; configure git user.name/email. '+committed.stderr.strip())
    branch=git('branch','--show-current').stdout.strip()
    pushed=git('push','-u','origin',branch)
    if pushed.returncode: raise RuntimeError('Catalog committed locally but push failed: '+pushed.stderr.strip())
    print(f'Committed and pushed updated catalog on {branch}.')

def main():
    ap=argparse.ArgumentParser(description='Local reference checker and catalog')
    sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('process',help='Process files listed in a manifest (one path per line)'); p.add_argument('manifest'); p.add_argument('--no-online-check',action='store_true'); p.add_argument('--no-publish',action='store_true',help='Do not commit and push this batch')
    s=sub.add_parser('serve',help='Start local search and export page'); s.add_argument('--port',type=int,default=8765)
    args=ap.parse_args()
    if args.cmd=='process':
        manifest=Path(args.manifest).expanduser().resolve()
        paths=[]
        for line in manifest.read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#'): continue
            source=Path(line).expanduser()
            paths.append(str(source if source.is_absolute() else manifest.parent/source))
        process(paths,not args.no_online_check,not args.no_publish)
    else: serve(args.port)

PAGE='''<!doctype html><meta charset="utf-8"><title>Reference Catalog</title><style>body{font:16px system-ui;max-width:1200px;margin:30px auto;padding:0 18px;color:#19212b}input,select,button{font:inherit;padding:9px;margin:4px}input{width:min(680px,85%)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.box{border:1px solid #ccd3dc;border-radius:10px;padding:14px;min-height:280px}.item{padding:10px;border-bottom:1px solid #ddd;cursor:pointer}.item:hover{background:#eef5ff}.small{color:#526070;font-size:14px}button{cursor:pointer}</style><h1>Reference catalog</h1><p>Search by title, author, venue, type, or source filename. Select results to build an export list.</p><input id="q" placeholder="Start typing a title, author, venue, or filename" autocomplete="off"><div class="grid"><section class="box"><h2>Matches</h2><div id="hits"></div></section><section class="box"><h2>Selected references <span id="count"></span></h2><div><button onclick="exp('text')">Text</button><button onclick="exp('csv')">CSV</button><button onclick="exp('bibtex')">BibTeX</button><button onclick="clearAll()">Clear</button></div><div id="sel"></div><textarea id="out" style="width:98%;height:90px;margin-top:8px" placeholder="Export preview"></textarea></section></div><script>let selected=[];const esc=s=>String(s||'').replace(/[&<>\"]/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[x]));async function search(){let q=document.querySelector('#q').value;let a=await fetch('/api/search?q='+encodeURIComponent(q));let rows=await a.json();document.querySelector('#hits').innerHTML=rows.map(x=>`<div class="item" onclick="add(${x.id})"><b>${esc(x.title||x.citation)}</b><div class="small">${esc(x.authors)} · ${esc(x.venue)} · ${esc(x.year)} · ${esc(x.kind)}<br>Source: ${esc(x.source_file)}</div></div>`).join('')||'<p class="small">No matches.</p>'}async function add(id){if(selected.includes(id))return;let r=await fetch('/api/item?id='+id);selected.push(id);paint()}async function paint(){let rows=await Promise.all(selected.map(async id=>await(await fetch('/api/item?id='+id)).json()));document.querySelector('#count').textContent='('+rows.length+')';document.querySelector('#sel').innerHTML=rows.map((x,i)=>`<div class="item"><b>${esc(x.title||x.citation)}</b><div class="small">${esc(x.authors)} · ${esc(x.venue)} · ${esc(x.year)}</div><button onclick="selected.splice(${i},1);paint()">Remove</button></div>`).join('')}async function exp(format){let r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:selected,format})});document.querySelector('#out').value=await r.text()}function clearAll(){selected=[];paint();document.querySelector('#out').value=''}document.querySelector('#q').addEventListener('input',search);search();</script>'''

def serve(port):
    connect().close()
    class H(BaseHTTPRequestHandler):
        def send(self,body,typ='application/json'):
            b=body.encode(); self.send_response(200); self.send_header('Content-Type',typ+'; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Access-Control-Allow-Origin','null'); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            from urllib.parse import urlparse,parse_qs
            u=urlparse(self.path); q=parse_qs(u.query)
            if u.path=='/': return self.send(PAGE,'text/html')
            c=connect()
            if u.path=='/api/search':
                term='%'+q.get('q',[''])[0]+'%'; rows=c.execute('SELECT id,citation,title,authors,venue,year,kind,source_file FROM refs WHERE title LIKE ? OR authors LIKE ? OR venue LIKE ? OR source_file LIKE ? OR citation LIKE ? ORDER BY title LIMIT 50',(term,)*5).fetchall(); return self.send(json.dumps([dict(x) for x in rows]))
            if u.path=='/api/item':
                row=c.execute('SELECT * FROM refs WHERE id=?',(q.get('id',['0'])[0],)).fetchone(); return self.send(json.dumps(dict(row) if row else {}))
            self.send('{}')
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0)))); c=connect(); ids=data.get('ids',[]); rows=[dict(r) for r in c.execute('SELECT * FROM refs WHERE id IN ('+','.join('?'*len(ids))+')',ids)] if ids else []; fmt=data.get('format')
            if fmt=='csv':
                import io; out=io.StringIO(); w=csv.writer(out); w.writerow(['citation','title','authors','venue','year','kind','source_file','doi','url']); [w.writerow([r.get(k,'') for k in ['citation','title','authors','venue','year','kind','source_file','doi','url']]) for r in rows]; return self.send(out.getvalue(),'text/csv')
            if fmt=='bibtex':
                def key(r): return re.sub(r'\W','',((r.get('authors') or 'ref').split()[-1]+(r.get('year') or '')+(r.get('title') or '')[:12]))
                out=[]
                for r in rows:
                    typ='article' if r['kind']=='Journal paper' else 'inproceedings' if r['kind']=='Conference paper' else 'misc'; fields={'title':r['title'],'author':r['authors'],'journal' if typ=='article' else 'booktitle':r['venue'],'year':r['year'],'doi':r['doi'],'url':r['url']}; out.append('@'+typ+'{'+key(r)+',\n'+',\n'.join('  '+k+' = {'+str(v)+'}' for k,v in fields.items() if v)+'\n}')
                return self.send('\n\n'.join(out),'text/plain')
            return self.send('\n'.join(r['citation'] for r in rows),'text/plain')
        def log_message(self,*a): pass
    print(f'Open http://127.0.0.1:{port}'); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__': main()
