#!/usr/bin/env python3
"""Desktop front end for Prism.

Double-clicking the app with no files opens this: a small local web server plus
the default browser. A browser is used rather than Tk because it looks the same
on macOS and Windows, needs nothing installed, and can actually be styled.

Files are chosen through the NATIVE picker, not an upload, so real paths are
kept: output lands next to the original exactly as it does when files are
dropped on the icon, and a 20MB model never has to move.
"""
import http.server, json, os, re, secrets, socket, subprocess, sys, threading
import time, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, 'optimise3mf.py')
PY = sys.executable or 'python3'
TOKEN = secrets.token_urlsafe(16)
# Set this to your own page to show a support link in the window and the README.
# Left as the placeholder it renders nothing, so a wrong link can never ship.
SUPPORT_URL = 'https://buymeacoffee.com/SET-ME'
IDLE_TIMEOUT = 45.0
_last_seen = [time.time()]


def engine(args, timeout=900):
    p = subprocess.run([PY, ENGINE] + args, capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def pick_files():
    """Native multi-select file dialog. Returns absolute paths."""
    if sys.platform == 'darwin':
        script = ('try\n'
                  'set fs to choose file with prompt "Choose 3MF files to optimise"'
                  ' of type {"3mf"} with multiple selections allowed\n'
                  'set out to ""\n'
                  'repeat with f in fs\n'
                  'set out to out & POSIX path of f & linefeed\n'
                  'end repeat\n'
                  'return out\n'
                  'on error number -128\n'
                  'return ""\n'
                  'end try')
        p = subprocess.run(['osascript', '-e', script],
                           capture_output=True, text=True)
        return [l for l in p.stdout.splitlines() if l.strip()]
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$d = New-Object System.Windows.Forms.OpenFileDialog;"
          "$d.Filter = '3MF files (*.3mf)|*.3mf';"
          "$d.Multiselect = $true;"
          "if ($d.ShowDialog() -eq 'OK') { $d.FileNames -join [Environment]::NewLine }")
    p = subprocess.run(['powershell', '-NoProfile', '-STA', '-Command', ps],
                       capture_output=True, text=True)
    return [l for l in p.stdout.splitlines() if l.strip()]


def reveal(path):
    """Show the finished file in Finder or Explorer.

    Explorer wants '/select,' and the path as ONE argument. Passed as two it
    silently ignores the selection and just opens a window. It also returns a
    non-zero exit code on success, so the result is not checked."""
    try:
        if sys.platform == 'darwin':
            subprocess.run(['open', '-R', path])
        else:
            subprocess.run('explorer /select,"%s"' % os.path.normpath(path),
                           shell=True)
    except Exception:
        pass


def printers():
    rc, out, _ = engine(['--list'])
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        key = line.split()[0]
        label = line[len(key):].strip()
        spectrum = '[' in label
        rows.append({'key': key,
                     'label': re.sub(r'\s*\[.*\]\s*$', '', label).strip(),
                     'spectrum': spectrum})
    return rows


def palette(key):
    rc, out, _ = engine(['--printer', key, '--spectrum-list'])
    rows = []
    for line in out.splitlines()[1:]:
        m = re.match(r'\s*(\d+)\s+(#[0-9A-Fa-f]{6})\s+(.*)$', line)
        if m:
            rows.append({'id': int(m.group(1)), 'hex': m.group(2),
                         'label': m.group(3).replace('(loaded filament)', '').strip(),
                         'solid': 'loaded filament' in m.group(3)})
    return rows


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Prism</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14161a;--dim:#6b7280;--line:#e3e6ea;
--accent:#2f7d62;--accentink:#fff;--warn:#9a6700;--bad:#b42318;--radius:14px}
@media(prefers-color-scheme:dark){:root{--bg:#15171b;--card:#1d2026;--ink:#e9ecf1;
--dim:#98a0ad;--line:#2c313a;--accent:#4aa588;--accentink:#08130f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,
BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;padding:28px 20px 60px}
.wrap{max-width:780px;margin:0 auto}
h1{font-size:21px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
padding:18px 20px;margin-bottom:14px}
.card.off{opacity:.45;pointer-events:none}
.step{display:flex;align-items:center;gap:9px;margin-bottom:12px}
.num{width:21px;height:21px;border-radius:50%;background:var(--accent);
color:var(--accentink);font-size:12px;font-weight:600;display:grid;place-items:center;flex:none}
.step h2{font-size:14px;margin:0;font-weight:600}
button{font:inherit;border-radius:9px;border:1px solid var(--line);background:var(--card);
color:var(--ink);padding:9px 15px;cursor:pointer}
button:hover{border-color:var(--accent)}
.primary{background:var(--accent);color:var(--accentink);border-color:var(--accent);
font-weight:600;padding:11px 22px}
.primary:disabled{opacity:.4;cursor:default}
select{font:inherit;padding:9px 11px;border-radius:9px;border:1px solid var(--line);
background:var(--card);color:var(--ink);width:100%}
.files{margin-top:11px;font-size:13px;color:var(--dim)}
.files div{padding:3px 0;word-break:break-all}
.modes{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}
.mode{border:1px solid var(--line);border-radius:11px;padding:12px;cursor:pointer;
text-align:left;background:var(--card)}
.mode.sel{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.mode b{display:block;font-size:13px;margin-bottom:3px}
.mode span{font-size:11.5px;color:var(--dim);line-height:1.35;display:block}
pre{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px;
font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-x:auto;
white-space:pre-wrap;margin:0}
.sw{display:grid;grid-template-columns:repeat(auto-fill,minmax(138px,1fr));gap:7px}
.swhead{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);
font-weight:600;margin:15px 0 8px}
.swhead:first-child{margin-top:11px}
.chip{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:9px;
padding:7px 9px;cursor:pointer;background:var(--card);text-align:left;font-size:12px}
.chip.sel{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.dot{width:19px;height:19px;border-radius:5px;flex:none;border:1px solid rgba(128,128,128,.4)}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.tog{display:flex;align-items:center;gap:9px;cursor:pointer;font-size:13.5px}
.tog input{width:17px;height:17px;accent-color:var(--accent)}
.hint{font-size:12px;color:var(--dim);margin-top:9px}
.out{font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
white-space:pre-wrap;word-break:break-word}
.ok{color:var(--accent);font-weight:600}.bad{color:var(--bad);font-weight:600}
.foot{margin:26px 0 0;text-align:center;font-size:12.5px;color:var(--dim)}
.foot a{color:var(--accent);font-weight:600}
.spin{width:15px;height:15px;border:2px solid var(--line);border-top-color:var(--accent);
border-radius:50%;animation:s .7s linear infinite;display:inline-block;vertical-align:-2px}
@keyframes s{to{transform:rotate(360deg)}}
</style></head><body><div class="wrap">
<h1>Prism</h1>
<p class="sub">Retarget any 3MF. Blend any colour.  &middot;  24 printers, four slicer dialects, Full Spectrum on the Snapmaker&nbsp;U1.</p>

<div class="card" id="c1"><div class="step"><div class="num">1</div><h2>Choose files</h2></div>
<div class="row"><button id="pick">Choose 3MF files…</button>
<span class="hint" id="filehint" style="margin:0">Nothing chosen yet</span></div>
<div class="files" id="files"></div></div>

<div class="card off" id="c2"><div class="step"><div class="num">2</div><h2>Printer</h2></div>
<select id="printer"></select></div>

<div class="card off" id="c3"><div class="step"><div class="num">3</div><h2>Model analysis</h2></div>
<pre id="report">—</pre>
<div class="modes" style="margin-top:12px" id="modes"></div></div>

<div class="card off" id="c4"><div class="step"><div class="num">4</div><h2>Colour</h2></div>
<label class="tog"><input type="checkbox" id="fs"><span>Use Full Spectrum: blend four filaments into a wider palette</span></label>
<div id="fsbody" style="display:none">
<div class="hint" id="fshint"></div>
<div id="swwrap"></div></div></div>

<div class="card" id="c5"><button class="primary" id="go" disabled>Convert</button>
<span class="hint" id="gohint" style="margin-left:11px"></span>
<div class="out" id="out" style="margin-top:14px"></div></div>
<p class="foot" __SUPPORT__>Prism is free and open source.
<a href="__URL__" target="_blank" rel="noopener">Buy me a coffee</a> if it saved you a reprint.</p>
</div><script>
const T=new URLSearchParams(location.search).get('t');
const api=(p,b)=>fetch(p+'?t='+T,{method:b?'POST':'GET',headers:{'Content-Type':'application/json'},
  body:b?JSON.stringify(b):null}).then(r=>r.json());
let S={files:[],printer:null,mode:'balanced',spectrum:false,colour:null,palette:[],spectrumOk:false};
setInterval(()=>api('/api/ping').catch(()=>{}),8000);

const MODES=[['speed','Speed','Coarser layers, about 0.7× the time'],
 ['balanced','Balanced','The sensible default'],
 ['quality','Quality','Finest layers, ironing on big flat tops']];
document.getElementById('modes').innerHTML=MODES.map(([k,n,d])=>
 `<button class="mode${k==='balanced'?' sel':''}" data-m="${k}"><b>${n}</b><span>${d}</span></button>`).join('');
document.getElementById('modes').onclick=e=>{const b=e.target.closest('.mode');if(!b)return;
 S.mode=b.dataset.m;[...document.querySelectorAll('.mode')].forEach(x=>x.classList.toggle('sel',x===b));};

api('/api/printers').then(r=>{const s=document.getElementById('printer');
 s.innerHTML='<option value="">Select a printer…</option>'+r.printers.map(p=>
  `<option value="${p.key}" data-s="${p.spectrum?1:0}">${p.label}${p.spectrum?'  ·  Full Spectrum':''}</option>`).join('');
 s.onchange=()=>{S.printer=s.value||null;
  S.spectrumOk=s.selectedOptions[0]&&s.selectedOptions[0].dataset.s==='1';
  document.getElementById('c4').classList.toggle('off',!S.spectrumOk);
  if(!S.spectrumOk){S.spectrum=false;document.getElementById('fs').checked=false;
   document.getElementById('fsbody').style.display='none';}
  else loadPalette();
  refresh();analyse();};});

function loadPalette(){api('/api/palette',{printer:S.printer}).then(r=>{S.palette=r.palette;
 const chip=c=>`<button class="chip" data-c="${c.id}"><div class="dot" style="background:${c.hex}"></div>${c.label}</button>`;
 const solids=r.palette.filter(c=>c.solid), blends=r.palette.filter(c=>!c.solid);
 document.getElementById('swwrap').innerHTML=
  `<div class="sw" style="margin-top:11px"><button class="chip sel" data-c=""><div class="dot" style="background:linear-gradient(135deg,#08ABFB,#F9ED3D 50%,#D93B90)"></div>Map automatically</button></div>`+
  `<div class="swhead">Loaded filaments</div><div class="sw">${solids.map(chip).join('')}</div>`+
  `<div class="swhead">Blends</div><div class="sw">${blends.map(chip).join('')}</div>`;});}
document.getElementById('swwrap').onclick=e=>{const b=e.target.closest('.chip');if(!b)return;
 S.colour=b.dataset.c||null;[...document.querySelectorAll('.chip')].forEach(x=>x.classList.toggle('sel',x===b));};

document.getElementById('fs').onchange=e=>{S.spectrum=e.target.checked;
 document.getElementById('fsbody').style.display=S.spectrum?'block':'none';
 if(S.spectrum)probe();refresh();};

function probe(){api('/api/probe',{files:S.files}).then(r=>{
 document.getElementById('fshint').textContent=r.colours>1
  ?`This file carries ${r.colours} colours. Mapping matches each to the nearest blend, or pick one colour for the whole model.`
  :'This file is a single colour, so there is nothing to map. Pick the colour to print it in.';});}

document.getElementById('pick').onclick=()=>api('/api/pick',{}).then(r=>{
 if(!r.files.length)return; S.files=r.files;
 document.getElementById('filehint').textContent=r.files.length+' file'+(r.files.length>1?'s':'');
 document.getElementById('files').innerHTML=r.files.map(f=>'<div>'+f.split('/').pop().split('\\').pop()+'</div>').join('');
 document.getElementById('c2').classList.remove('off');refresh();analyse();if(S.spectrum)probe();});

function analyse(){if(!S.files.length||!S.printer)return;
 document.getElementById('c3').classList.remove('off');
 document.getElementById('report').innerHTML='<span class="spin"></span> analysing…';
 api('/api/report',{printer:S.printer,files:S.files}).then(r=>{
  document.getElementById('report').textContent=r.text.trim()||'no analysis available';});}

function refresh(){document.getElementById('go').disabled=!(S.files.length&&S.printer);}

document.getElementById('go').onclick=()=>{const g=document.getElementById('go');
 g.disabled=true;document.getElementById('gohint').innerHTML='<span class="spin"></span> converting…';
 document.getElementById('out').textContent='';
 api('/api/convert',{files:S.files,printer:S.printer,mode:S.mode,
   spectrum:S.spectrum,colour:S.colour}).then(r=>{
  document.getElementById('gohint').textContent='';
  const o=document.getElementById('out');o.innerHTML='';
  const h=document.createElement('div');
  h.innerHTML=r.ok?'<span class="ok">Done.</span>':'<span class="bad">Something went wrong.</span>';
  o.appendChild(h);
  const p=document.createElement('pre');p.style.marginTop='9px';p.textContent=r.text.trim();o.appendChild(p);
  if(r.outputs&&r.outputs.length){const b=document.createElement('button');
   b.textContent='Show in '+(r.mac?'Finder':'Explorer');b.style.marginTop='11px';
   b.onclick=()=>api('/api/reveal',{path:r.outputs[0]});o.appendChild(b);}
  g.disabled=false;});};
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _auth(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        return q.get('t', [''])[0] == TOKEN

    def _send(self, body, ctype='application/json'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _last_seen[0] = time.time()
        path = self.path.split('?')[0]
        if not self._auth():
            self.send_error(403)
            return
        if path == '/':
            page = PAGE
            if 'SET-ME' in SUPPORT_URL:
                page = page.replace('__SUPPORT__', 'style="display:none"')
            else:
                page = page.replace('__SUPPORT__', '').replace('__URL__', SUPPORT_URL)
            self._send(page, 'text/html; charset=utf-8')
        elif path == '/api/printers':
            self._send(json.dumps({'printers': printers()}))
        elif path == '/api/ping':
            self._send(json.dumps({'ok': True}))
        else:
            self.send_error(404)

    def do_POST(self):
        _last_seen[0] = time.time()
        path = self.path.split('?')[0]
        if not self._auth():
            self.send_error(403)
            return
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            body = {}
        files = [f for f in (body.get('files') or []) if os.path.exists(f)]
        try:
            if path == '/api/pick':
                self._send(json.dumps({'files': pick_files()}))
            elif path == '/api/palette':
                self._send(json.dumps({'palette': palette(body.get('printer', ''))}))
            elif path == '/api/probe':
                rc, out, _ = engine(['--spectrum-probe'] + files)
                self._send(json.dumps({'colours': int((out.strip() or '0').split()[0])}))
            elif path == '/api/report':
                rc, out, err = engine(['--printer', body.get('printer', ''),
                                       '--report'] + files)
                self._send(json.dumps({'text': out or err}))
            elif path == '/api/reveal':
                reveal(body.get('path', ''))
                self._send(json.dumps({'ok': True}))
            elif path == '/api/ping':
                self._send(json.dumps({'ok': True}))
            elif path == '/api/convert':
                args = ['--printer', body.get('printer', ''),
                        '--mode', body.get('mode', 'balanced')]
                if body.get('spectrum'):
                    args.append('--spectrum')
                    if body.get('colour'):
                        args += ['--spectrum-colour', str(body['colour'])]
                rc, out, err = engine(args + files)
                outs = re.findall(r'^OK -> (.+)$', out, re.M)
                self._send(json.dumps({'ok': rc == 0, 'text': out + err,
                                       'outputs': outs,
                                       'mac': sys.platform == 'darwin'}))
            else:
                self.send_error(404)
        except Exception as exc:
            self._send(json.dumps({'ok': False, 'text': '%s: %s'
                                   % (type(exc).__name__, exc), 'outputs': []}))


def main():
    if not os.path.exists(ENGINE):
        sys.exit('engine not found next to gui.py')
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:%d/?t=%s' % (port, TOKEN)
    print('Prism running at', url)
    webbrowser.open(url)
    try:
        while time.time() - _last_seen[0] < IDLE_TIMEOUT:
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    srv.shutdown()


if __name__ == '__main__':
    main()
