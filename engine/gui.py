#!/usr/bin/env python3
"""Desktop front end for Prism.

Double-clicking the app with no files opens this: a small local web server plus
the default browser. A browser is used rather than Tk because it looks the same
on macOS and Windows, needs nothing installed, and can actually be styled.

Files are chosen through the NATIVE picker, not an upload, so real paths are
kept: output lands next to the original exactly as it does when files are
dropped on the icon, and a 20MB model never has to move.
"""
import http.server, json, os, re, secrets, shutil, socket, subprocess, sys, threading
import time, webbrowser

# Frozen into a single Windows .exe, sys.executable IS that exe and there is no
# optimise3mf.py on disk, so the engine is re-entered through the exe itself
# with a sentinel argument. From source it stays a plain python call.
FROZEN = bool(getattr(sys, 'frozen', False))
HERE = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, 'optimise3mf.py')
PY = sys.executable or 'python3'
ENGINE_CMD = [sys.executable, '--engine'] if FROZEN else [PY, ENGINE]
TOKEN = secrets.token_urlsafe(16)
# Set this to your own page to show a support link in the window and the README.
# Left as the placeholder it renders nothing, so a wrong link can never ship.
SUPPORT_URL = 'https://buymeacoffee.com/prismprints'
# A hidden browser tab has its timers throttled to roughly once a minute and
# frozen entirely after a few minutes, so the heartbeat is not proof of life and
# its absence is not proof of death. The old 45s timeout meant looking at another
# window for a minute killed the app. This is now only a safety net for a browser
# that crashed or was force quit, and closing the tab is handled explicitly by
# the goodbye beacon instead of by waiting for silence.
IDLE_TIMEOUT = 900.0
# A reload fires pagehide too, so the beacon starts a countdown rather than
# quitting outright. The request the reloaded page makes cancels it.
GOODBYE_GRACE = 20.0
_last_seen = [time.time()]
_leaving = [0.0]


def engine(args, timeout=900):
    kw = {}
    if FROZEN and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        kw['creationflags'] = subprocess.CREATE_NO_WINDOW   # no console flash
    p = subprocess.run(ENGINE_CMD + args, capture_output=True, text=True,
                       timeout=timeout, **kw)
    return p.returncode, p.stdout, p.stderr


# Linux has no single native dialog, so the usual three are tried in turn.
# Each returns one path per line; a desktop without any of them gets a clear
# message rather than a button that silently does nothing.
LINUX_PICKERS = [
    ['zenity', '--file-selection', '--multiple', '--separator=\n',
     '--title=Choose files', '--file-filter=Models | *.3mf *.3MF *.obj *.OBJ *.stl *.STL'],
    ['kdialog', '--getopenfilename', '.', '*.3mf *.obj *.stl', '--multiple',
     '--separate-output'],
    ['yad', '--file', '--multiple', '--separator=\n',
     '--file-filter=Models | *.3mf *.obj *.stl'],
]


def pick_files():
    """Native multi-select file dialog. Returns absolute paths."""
    if sys.platform.startswith('linux'):
        for cmd in LINUX_PICKERS:
            if not shutil.which(cmd[0]):
                continue
            p = subprocess.run(cmd, capture_output=True, text=True)
            return [l for l in p.stdout.replace('|', '\n').splitlines()
                    if l.strip() and os.path.exists(l.strip())]
        return []
    if sys.platform == 'darwin':
        script = ('try\n'
                  'set fs to choose file with prompt "Choose models to optimise"'
                  ' of type {"3mf", "obj", "stl"} with multiple selections allowed\n'
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
          "$d.Filter = 'Models (*.3mf;*.obj;*.stl)|*.3mf;*.obj;*.stl';"
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
        elif sys.platform.startswith('linux'):
            # no universal "reveal and select", so open the containing folder
            subprocess.run(['xdg-open', os.path.dirname(os.path.abspath(path))])
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
        sl = ''
        if '->' in label:
            label, sl = label.split('->', 1)
            sl = sl.strip()
        rows.append({'key': key,
                     'label': re.sub(r'\s*\[.*\]\s*$', '', label).strip(),
                     'spectrum': spectrum, 'slicer': sl})
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


# key, label, kind, the slicer's own name for it, and what it actually does.
# The last field is the point: most people meet these settings without ever
# being told what they change, so the panel explains rather than just exposes.
QUICK = [
 ('layer_height', 'Layer height', 'num', 'Layer height',
  'How thick each printed layer is, and the biggest single lever on both '
  'quality and time. Thinner means smoother curves and finer detail, and '
  'proportionally longer: halving it roughly doubles the print. It does '
  'nothing for detail sideways, only vertically. NOTE: the Speed, Balanced '
  'and Quality buttons above set this for you. Putting a value here overrides '
  'whichever you picked, so leave it blank unless you want a specific height.'),
 ('fan_max_speed', 'Part cooling fan', 'pctslider', 'Fan max speed',
  'The fan blowing on the part as it prints. Cooling sets plastic fast, which '
  'helps overhangs and fine detail, but too much of it weakens the bond between '
  'layers and can warp or crack taller prints. The U1 fans are strong; if walls '
  'are splitting or corners lifting, this is the first thing to bring down.'),
 ('additional_cooling_fan_speed', 'Auxiliary fan', 'pctslider',
  'Additional cooling fan speed',
  'The second, larger fan that cools the whole chamber rather than the nozzle '
  'area. Useful on PLA, usually unwanted on ABS and ASA where a warm chamber is '
  'what stops the part splitting.'),
 ('overhang_fan_speed', 'Overhang fan', 'pctslider', 'Overhang fan speed',
  'A separate speed used only over overhangs and bridges, where the plastic has '
  'nothing underneath and has to set in the air. Usually higher than the main '
  'fan, and worth keeping high even if you turn the main one down.'),
 ('sparse_infill_pattern', 'Infill pattern', 'enum', 'Sparse infill pattern',
  'The lattice inside the part. Gyroid is equally strong in every direction and '
  'never crosses itself, so it prints cleanly and quietly. Grid is quicker but '
  'weaker across layers and can rattle where the lines cross.'),
 ('sparse_infill_density', 'Infill density', 'pct', 'Sparse infill density',
  'How much of the inside is filled. 15% is plenty for something you look at. '
  'Past roughly 40% you gain weight and print time faster than you gain '
  'strength, and extra walls would serve you better.'),
 ('wall_loops', 'Walls', 'int', 'Wall loops',
  'How many perimeters make the skin. Adding a wall buys far more strength than '
  'adding infill, for less time. Two is normal, three for anything load bearing.'),
 ('wall_generator', 'Wall generator', 'enum', 'Wall generator',
  'The algorithm that lays out the perimeters. Arachne varies wall width to '
  'fit thin features, so lettering, thin ribs and sharp corners come out '
  'properly instead of being skipped where the geometry is narrower than one '
  'wall. Classic keeps every wall the same width. Prefer Arachne unless '
  'something specific misbehaves; it costs nothing.'),
 ('top_shell_layers', 'Top layers', 'int', 'Top shell layers',
  'Solid layers closing the top. Too few and the infill shows through as '
  'pinholes or a quilted texture. Five is a safe default at 0.2mm.'),
 ('bottom_shell_layers', 'Bottom layers', 'int', 'Bottom shell layers',
  'Solid layers on the underside. Mostly cosmetic unless the part is thin, '
  'where too few makes it flex.'),
 ('enable_support', 'Supports', 'bool', 'Enable support',
  'Whether anything is printed to hold up overhangs. Prism can work this out '
  'from the model itself, adding them only where the geometry cannot hold '
  'itself up.'),
 ('support_type', 'Support type', 'enum', 'Support type',
  'What kind of scaffolding is built under overhangs, if any. Normal supports '
  'are simple columns and easy to remove. Tree supports branch up to only the '
  'points that need them, touch far less of the surface and peel away more '
  'cleanly, which suits figures and organic shapes. Supports are material and '
  'time you throw away, and they always mark whatever they touch.'),
 ('support_threshold_angle', 'Support threshold', 'int', 'Support threshold angle',
  'Overhangs shallower than this angle get supported. Lower means fewer '
  'supports and more trust in the printer to bridge. 30 degrees is the usual '
  'setting; a well tuned machine often manages 40.'),
 ('support_interface_top_layers', 'Support interface layers', 'int',
  'Top interface layers',
  'The dense raft between the support and the part above it. More layers give a '
  'cleaner surface underneath, but make the support harder to snap off.'),
 ('support_top_z_distance', 'Support top gap (mm)', 'num', 'Top Z distance',
  'The air gap between the support and the part it holds up. Larger releases '
  'more easily and leaves a rougher face; smaller leaves a better face and can '
  'fuse. Best kept to a multiple of your layer height.'),
 ('support_style', 'Support style', 'enum', 'Support style',
  'Tree supports use less material and touch the model in fewer places, which '
  'is kinder to the surface. Grid is more reliable under a large flat ceiling.'),
 ('support_on_build_plate_only', 'Supports from the plate only', 'bool',
  'Support on build plate only',
  'Whether supports may stand on the model itself or only rise from the bed. '
  'On is the safer choice: a support resting on the model always marks it, and '
  'those are the ones that wobble and fail partway up, taking the print with '
  'them. Turn it off only when a feature genuinely overhangs another part of '
  'the same model with no path down to the plate.'),
 ('brim_type', 'Brim', 'enum', 'Brim type',
  'A flat skirt printed around the first layer to hold the part down. Worth it '
  'for tall thin parts, small footprints and anything prone to lifting at the '
  'corners. It is the cheapest insurance against a print coming loose: a few '
  'grams and a little cleanup against losing the whole thing.'),
 ('brim_width', 'Brim width', 'num', 'Brim width',
  'How far the brim reaches out from the part. 3 to 5mm handles most adhesion '
  'trouble. Wider rarely helps if the real problem is the first layer itself, '
  'and leaves more to cut away afterwards.'),
 ('seam_position', 'Seam position', 'enum', 'Seam position',
  'Where each layer starts and stops, visible as a faint line up the side. '
  'Aligned stacks them into one tidy seam you can hide; random scatters them so '
  'none of them stands out.'),
 ('ironing_type', 'Ironing', 'enum', 'Ironing type',
  'Runs the hot nozzle back over flat top surfaces to smooth them. It costs '
  'real time, so it earns its place on large flat tops and nowhere else.'),
]


def settings_for(key):
    """What this printer ships, what Prism changes, and what each may be set to.

    Read straight from the baked profile rather than parsed out of CLI output,
    so the controls can never drift from what the engine will accept."""
    path = os.path.join(HERE, 'data', 'printers', key + '.json')
    if not os.path.exists(path):
        return {'rows': []}
    with open(path, encoding='utf-8') as fh:
        d = json.load(fh)
    tpl, enums, defaults = d['template'], d.get('enums', {}), d.get('defaults', {})
    rows = []
    for k, label, kind, slicer_name, helptext in QUICK:
        if k not in tpl:
            continue
        cur = tpl[k]
        cur = cur[0] if isinstance(cur, list) and cur else cur
        cur = str(cur).rstrip('%')
        rows.append({'key': k, 'label': label, 'kind': kind,
                     'slicer': slicer_name, 'help': helptext,
                     'effective': defaults.get(k, cur),
                     'prism': defaults.get(k),
                     'options': sorted(enums.get(k, []))})
    return {'rows': rows}


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
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:11px;margin-top:13px}
.fld{display:flex;flex-direction:column;gap:5px}
.lbl{font-size:12.5px;color:var(--dim);font-weight:500;display:block}
.fld input,.fld select,textarea{font:inherit;font-size:13.5px;padding:7px 9px;
 border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);width:100%}
textarea{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;resize:vertical}
.mark{color:var(--accent);font-weight:600}
.sl{display:flex;align-items:center;gap:10px}
.sl input[type=range]{flex:1;accent-color:var(--accent);min-width:0}
.sl output{font:500 12.5px "IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;
 color:var(--dim);width:42px;text-align:right;flex:none}
.i{display:inline-grid;place-items:center;width:15px;height:15px;border-radius:50%;
 border:1px solid var(--line);color:var(--dim);font-size:10px;font-weight:700;
 cursor:pointer;margin-left:5px;vertical-align:1px;background:var(--card);
 font-family:Georgia,serif;font-style:italic;line-height:1}
.i:hover{border-color:var(--accent);color:var(--accent)}
.hlp{display:none;font-size:12px;line-height:1.5;color:var(--dim);
 background:var(--bg);border:1px solid var(--line);border-left:3px solid var(--accent);
 border-radius:0 8px 8px 0;padding:9px 11px;margin-top:6px}
.hlp.on{display:block}
.hlp b{color:var(--ink);font-weight:600}
summary{cursor:pointer;font-size:14px;font-weight:600;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ ";color:var(--dim)}
details[open] summary::before{content:"▾ "}
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

<div class="card off" id="cimp"><div class="step"><div class="num">!</div><h2>About this model</h2></div>
<p class="hint">An OBJ or STL carries shape and nothing else. These two answers
are not in the file, and both look right when they are wrong, so Prism will not
guess them.</p>
<div id="impbody"></div></div>

<div class="card off" id="c2"><div class="step"><div class="num">2</div><h2>Printer</h2></div>
<select id="printer"></select></div>

<div class="card off" id="c3"><div class="step"><div class="num">3</div><h2>Model analysis</h2></div>
<div class="row" style="margin-bottom:11px">
<label class="lbl" for="orient" style="margin:0">Orientation</label>
<select id="orient" style="width:auto;min-width:290px">
 <option value="">Leave it as placed</option>
 <option value="suggest">Tell me which way up needs least support</option>
 <option value="apply">Turn it for me</option>
</select></div>
<pre id="report">–</pre>
<div class="modes" style="margin-top:12px" id="modes"></div></div>

<div class="card off" id="c4"><div class="step"><div class="num">4</div><h2>Colour</h2></div>
<label class="tog"><input type="checkbox" id="fs"><span>Use Full Spectrum: blend four filaments into a wider palette</span></label>
<div id="fsbody" style="display:none">
<div class="hint" id="fshint"></div>
<div id="swwrap"></div>
<button id="cpbtn" type="button">Show what my colours become</button>
<pre id="cpout" hidden></pre></div></div>

<div class="card" id="csup"><div class="step"><div class="num">5</div><h2>Supports</h2></div>
<label class="tog"><input type="checkbox" id="sup"><span>Work out where supports are
actually needed, and add only those</span></label>
<p class="hint" id="suphint">Prism measures every overhang in the model: how far it
reaches, how steep it is and how high it sits. Anything the printer can bridge on its
own is left alone.</p></div>

<div class="card" id="chelp"><details id="helpd"><summary>Questions</summary>
<p class="hint">A printer profile carries hundreds of settings and the slicer
explains almost none of them. Ask about any of them by the name you know it by.</p>
<label for="exq">What does a setting do?</label>
<div class="row"><input id="exq" placeholder="infill, z distance, seam, fan"
 autocomplete="off"><button id="exbtn" type="button">Explain</button></div>
<pre id="exout" hidden></pre>
<label for="fxq">Something went wrong</label>
<div class="row"><input id="fxq" placeholder="failed at 80%, stringing, top looks rough"
 autocomplete="off"><button id="fxbtn" type="button">Diagnose</button></div>
<p class="hint">With files chosen above, this checks them rather than guessing.</p>
<pre id="fxout" hidden></pre>
</details></div>

<div class="card" id="c5"><details id="adv"><summary>Advanced settings<span id="advcount"></span></summary>
<p class="hint" style="margin-top:4px">Blank uses the value shown. Prism's own
choices are marked; clearing one back to blank restores it. Every setting has an
<span class="i" style="cursor:default">i</span> explaining what it changes in the
slicer and why it matters.</p>
<button id="explain" style="margin-top:2px">Explain every setting</button>
<p class="hint" id="advwait">These appear once you choose a printer above, because
what a setting accepts, and what it is set to now, are its answers and not
Prism's.</p>
<div class="grid" id="advgrid"></div>
<label class="lbl" for="extra" style="margin-top:14px">Anything else, one
<code>key=value</code> per line</label>
<textarea id="extra" rows="3" spellcheck="false"
 placeholder="ironing_type=topmost&#10;brim_width=5"></textarea>
</details></div>

<div class="card" id="c6"><button class="primary" id="go" disabled>Convert</button>
<span class="hint" id="gohint" style="margin-left:11px"></span>
<div class="out" id="out" style="margin-top:14px"></div></div>
<p class="foot" __SUPPORT__>Prism is free and open source.
<a href="__URL__" target="_blank" rel="noopener">Buy me a coffee</a> if it saved you a reprint.</p>
</div><script>
const T=new URLSearchParams(location.search).get('t');
const api=(p,b)=>fetch(p+'?t='+T,{method:b?'POST':'GET',headers:{'Content-Type':'application/json'},
  body:b?JSON.stringify(b):null}).then(r=>r.json());
let S={files:[],printer:null,mode:'balanced',spectrum:false,colour:null,palette:[],spectrumOk:false};
/* The questions card. Answers come from the engine, so the window and the
   command line can never drift apart on what a setting means. */
const ask=(btn,inp,out,ep,after)=>{
  const B=document.getElementById(btn),I=document.getElementById(inp),O=document.getElementById(out);
  const run=()=>{
    const term=(I.value||'').trim(); if(!term)return;
    O.hidden=false; O.textContent='...';
    api(ep,{term:term,printer:S.printer,files:S.files})
      .then(r=>{O.textContent=r.text||'nothing came back'; if(after)after(r.text||'');})
      .catch(()=>{O.textContent='could not reach the engine';});
  };
  B.addEventListener('click',run);
  I.addEventListener('keydown',e=>{if(e.key==='Enter')run();});
};
ask('exbtn','exq','exout','/api/explain');
ask('fxbtn','fxq','fxout','/api/fix',out=>{
  /* Setting names in a diagnosis are the next question, so make them the
     next click rather than something to retype. */
  const O=document.getElementById('fxout');
  O.innerHTML=O.textContent.replace(/^(\s{4})([a-z][a-z0-9_]{4,})$/gm,
    (m,sp,k)=>sp+'<a href="#" data-k="'+k+'">'+k+'</a>');
  O.querySelectorAll('a[data-k]').forEach(a=>a.addEventListener('click',e=>{
    e.preventDefault();
    document.getElementById('exq').value=a.dataset.k;
    document.getElementById('exbtn').click();
    document.getElementById('exout').scrollIntoView({block:'nearest'});
  }));
});
document.getElementById('cpbtn').addEventListener('click',()=>{
  const O=document.getElementById('cpout');
  O.hidden=false; O.textContent='...';
  api('/api/colourpreview',{printer:S.printer,files:S.files})
    .then(r=>{O.textContent=r.text||'nothing came back';})
    .catch(()=>{O.textContent='could not reach the engine';});
});

const beat=()=>api('/api/ping').catch(()=>{});
setInterval(beat,8000);
/* A hidden tab's timers are throttled and eventually frozen, so beat again the
   moment the page is looked at rather than waiting for the next tick. */
document.addEventListener('visibilitychange',()=>{if(!document.hidden)beat();});
window.addEventListener('focus',beat);
/* Closing the tab should quit the app promptly instead of leaving it running.
   pagehide also fires on a reload, so this only starts a countdown, and the
   reloaded page cancels it with its first request. */
window.addEventListener('pagehide',()=>{try{navigator.sendBeacon('/api/bye?t='+T);}catch(e){}});

const MODES=[['speed','Speed','Coarser layers, about 0.7× the time'],
 ['balanced','Balanced','The sensible default'],
 ['quality','Quality','Finest layers, ironing on big flat tops']];
document.getElementById('modes').innerHTML=MODES.map(([k,n,d])=>
 `<button class="mode${k==='balanced'?' sel':''}" data-m="${k}"><b>${n}</b><span>${d}</span></button>`).join('');
document.getElementById('modes').onclick=e=>{const b=e.target.closest('.mode');if(!b)return;
 S.mode=b.dataset.m;[...document.querySelectorAll('.mode')].forEach(x=>x.classList.toggle('sel',x===b));};

api('/api/printers').then(r=>{const s=document.getElementById('printer');
 s.innerHTML='<option value="">Select a printer…</option>'+r.printers.map(p=>
  `<option value="${p.key}" data-s="${p.spectrum?1:0}" data-sl="${p.slicer||''}">${p.label}${p.spectrum?'  ·  Full Spectrum':''}</option>`).join('');
 s.onchange=()=>{S.printer=s.value||null;
  S.spectrumOk=s.selectedOptions[0]&&s.selectedOptions[0].dataset.s==='1';
  S.slicer=(s.selectedOptions[0]&&s.selectedOptions[0].dataset.sl)||'';
  document.getElementById('gohint').textContent=S.slicer?('Opens in '+S.slicer):'';
  document.getElementById('c4').classList.toggle('off',!S.spectrumOk);
  if(!S.spectrumOk){S.spectrum=false;document.getElementById('fs').checked=false;
   document.getElementById('fsbody').style.display='none';}
  else loadPalette();
  loadSettings();refresh();analyse();};});

function loadSettings(){
 const wait=document.getElementById('advwait');
 if(!S.printer){wait.hidden=false;document.getElementById('advgrid').innerHTML='';return;}
 api('/api/settings',{printer:S.printer}).then(r=>{
  wait.hidden=!!(r.rows&&r.rows.length);
  if(!r.rows||!r.rows.length){
   wait.textContent='No settings could be read for this printer.';
   document.getElementById('advgrid').innerHTML='';return;}
  document.getElementById('advcount').textContent=' ('+r.rows.length+')';
  document.getElementById('advgrid').innerHTML=r.rows.map((f,i)=>{
   const mark=f.prism?' <span class="mark">Prism</span>':'';
   const ctl=f.kind==='pctslider'
    ? `<div class="sl"><input type="range" min="0" max="100" step="5"
         value="${f.effective}" data-k="${f.key}" data-def="${f.effective}">
       <output>${f.effective}%</output></div>`
    : f.kind==='enum'&&f.options.length
    ? `<select data-k="${f.key}"><option value="">${f.effective}</option>`+
      f.options.map(o=>`<option value="${o}">${o}</option>`).join('')+`</select>`
    : `<input data-k="${f.key}" placeholder="${f.effective}">`;
   return `<div class="fld"><label class="lbl">${f.label}${mark}`+
    `<span class="i" data-h="h${i}" title="What does this do?">i</span></label>`+
    `${ctl}<div class="hlp" id="h${i}">`+
    `<b>In the slicer: ${f.slicer}</b><br>${f.help}</div></div>`;
  }).join('');});}

function collectSets(){const out=[];
 document.querySelectorAll('#advgrid [data-k]').forEach(el=>{
  const v=(el.value||'').trim();
  if(el.type==='range'){ if(v!==el.dataset.def) out.push(el.dataset.k+'='+v); return; }
  if(v) out.push(el.dataset.k+'='+v);});
 (document.getElementById('extra').value||'').split('\n').forEach(l=>{
  l=l.trim(); if(l&&l.includes('=')) out.push(l);});
 return out;}

function loadPalette(){api('/api/palette',{printer:S.printer}).then(r=>{S.palette=r.palette;
 const chip=c=>`<button class="chip" data-c="${c.id}"><div class="dot" style="background:${c.hex}"></div>${c.label}</button>`;
 const solids=r.palette.filter(c=>c.solid), blends=r.palette.filter(c=>!c.solid);
 document.getElementById('swwrap').innerHTML=
  `<div class="sw" style="margin-top:11px"><button class="chip sel" data-c=""><div class="dot" style="background:linear-gradient(135deg,#08ABFB,#F9ED3D 50%,#D93B90)"></div>Map automatically</button></div>`+
  `<div class="swhead">Loaded filaments</div><div class="sw">${solids.map(chip).join('')}</div>`+
  `<div class="swhead">Blends</div><div class="sw">${blends.map(chip).join('')}</div>`;});}
document.getElementById('advgrid').oninput=e=>{
 if(e.target.type==='range'){const o=e.target.parentNode.querySelector('output');
  if(o)o.textContent=e.target.value+'%';}};
document.getElementById('advgrid').onclick=e=>{const b=e.target.closest('.i');
 if(!b)return; const h=document.getElementById(b.dataset.h);
 if(h) h.classList.toggle('on');};
document.getElementById('explain').onclick=()=>{
 const any=[...document.querySelectorAll('#advgrid .hlp')].some(x=>!x.classList.contains('on'));
 document.querySelectorAll('#advgrid .hlp').forEach(x=>x.classList.toggle('on',any));
 document.getElementById('explain').textContent=any?'Hide explanations':'Explain every setting';};
document.getElementById('swwrap').onclick=e=>{const b=e.target.closest('.chip');if(!b)return;
 S.colour=b.dataset.c||null;[...document.querySelectorAll('.chip')].forEach(x=>x.classList.toggle('sel',x===b));};

document.getElementById('orient').onchange=()=>{S.orient=document.getElementById('orient').value;analyse();};
document.getElementById('fs').onchange=e=>{S.spectrum=e.target.checked;
 document.getElementById('fsbody').style.display=S.spectrum?'block':'none';
 if(S.spectrum)probe();refresh();};

function probe(){api('/api/probe',{files:S.files}).then(r=>{
 document.getElementById('fshint').textContent=r.colours>1
  ?`This file carries ${r.colours} colours. Mapping matches each to the nearest blend, or pick one colour for the whole model.`
  :'This file is a single colour, so there is nothing to map. Pick the colour to print it in.';});}

document.getElementById('pick').onclick=()=>api('/api/pick',{}).then(r=>{
 if(r.note){document.getElementById('filehint').textContent=r.note;return;}
 if(!r.files.length)return; S.files=r.files;
 document.getElementById('filehint').textContent=r.files.length+' file'+(r.files.length>1?'s':'');
 document.getElementById('files').innerHTML=r.files.map(f=>'<div>'+f.split('/').pop().split('\\').pop()+'</div>').join('');
 document.getElementById('c2').classList.remove('off');meshCheck();refresh();analyse();if(S.spectrum)probe();});

/* An OBJ or STL needs two answers the file does not contain. Ask here, the
   same two the command line prompts for, from the same measurements. */
function meshCheck(){
 const mesh=S.files.filter(f=>/\.(obj|stl)$/i.test(f));
 const card=document.getElementById('cimp'), body=document.getElementById('impbody');
 S.units=null; S.up=null;
 if(!mesh.length){card.classList.add('off'); body.innerHTML=''; return;}
 card.classList.remove('off');
 body.innerHTML='<span class="spin"></span> measuring…';
 api('/api/meshinfo',{files:mesh}).then(r=>{
  const info=(r.info||[]);
  if(!info.length){body.textContent=r.error||'could not read it';return;}
  const i=info[0], bad=info.find(x=>x.error);
  if(bad){body.innerHTML='<span class="bad">'+bad.file+': '+bad.error+'</span>';return;}
  const dim=d=>d.map(n=>Math.round(n)).join(' x ')+'mm';
  let h='';
  h+='<label>What units was it drawn in?</label><div class="row" id="urow">';
  i.units.forEach(u=>{h+='<button class="chip" data-u="'+u.unit+'">'+u.unit+
     ' &middot; '+dim(u.dims)+(u.plausible?'':' (not printable)')+'</button>';});
  h+='</div>';
  h+='<label>Which way up is it?</label><div class="row" id="uprow">'+
     '<button class="chip" data-up="z">As it is &middot; '+dim(i.as_is)+'</button>'+
     '<button class="chip" data-up="y">Turn it Z up &middot; '+dim(i.turned)+'</button></div>';
  if(i.looks_y_up)h+='<p class="hint">It is taller across Y than Z, which usually means a Y up export.</p>';
  if(i.materials>1)h+='<p class="hint">'+i.materials+' materials, kept as separate objects so their colours carry over.</p>';
  (i.warnings||[]).forEach(w=>{h+='<p class="hint">'+w+'</p>';});
  body.innerHTML=h;
  /* Nothing is preselected. A default here would be a guess wearing a tick. */
  body.querySelectorAll('[data-u]').forEach(b=>b.onclick=()=>{
    S.units=b.dataset.u;
    body.querySelectorAll('[data-u]').forEach(x=>x.classList.remove('sel'));
    b.classList.add('sel');refresh();});
  body.querySelectorAll('[data-up]').forEach(b=>b.onclick=()=>{
    S.up=b.dataset.up;
    body.querySelectorAll('[data-up]').forEach(x=>x.classList.remove('sel'));
    b.classList.add('sel');refresh();});
 }).catch(()=>{body.textContent='could not measure it';});
}

function analyse(){if(!S.files.length||!S.printer)return;
 document.getElementById('c3').classList.remove('off');
 document.getElementById('report').innerHTML='<span class="spin"></span> analysing…';
 api('/api/report',{printer:S.printer,files:S.files,
   orient:!!S.orient}).then(r=>{
  document.getElementById('report').textContent=r.text.trim()||'no analysis available';});}

function refresh(){
 const needMesh=S.files.some(f=>/\.(obj|stl)$/i.test(f));
 const ready=S.files.length&&S.printer&&(!needMesh||(S.units&&S.up));
 document.getElementById('go').disabled=!ready;
 const gh=document.getElementById('gohint');
 if(needMesh&&S.files.length&&S.printer&&!(S.units&&S.up))
   gh.textContent='Answer the two questions above first.';
 else if(gh.textContent==='Answer the two questions above first.')gh.textContent='';
}

document.getElementById('go').onclick=()=>{const g=document.getElementById('go');
 g.disabled=true;document.getElementById('gohint').innerHTML='<span class="spin"></span> converting…';
 document.getElementById('out').textContent='';
 api('/api/convert',{files:S.files,printer:S.printer,mode:S.mode,
   spectrum:S.spectrum,colour:S.colour,sets:collectSets(),
   supports:document.getElementById('sup').checked?'auto':null,
   units:S.units||null,up:S.up||null,
   orient:S.orient||null}).then(r=>{
  document.getElementById('gohint').textContent='';
  const o=document.getElementById('out');o.innerHTML='';
  const h=document.createElement('div');
  h.innerHTML=r.ok
   ?'<span class="ok">Done.</span>'+(S.slicer?' Open the result in <b>'+S.slicer+'</b>.':'')
   :'<span class="bad">Something went wrong.</span>';
  o.appendChild(h);
  const p=document.createElement('pre');p.style.marginTop='9px';p.textContent=r.text.trim();o.appendChild(p);
  if(r.outputs&&r.outputs.length){const b=document.createElement('button');
   b.textContent=r.reveal_label;b.style.marginTop='11px';
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
        # compare_digest, not ==, because == returns as soon as two bytes
        # differ. The time it takes therefore reports how much of the token a
        # guess got right, which is enough to recover it one character at a
        # time. Raised by ryvin (github.com/ryvin/prism).
        return secrets.compare_digest(q.get('t', [''])[0].encode(), TOKEN.encode())

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
        _leaving[0] = 0.0
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
        elif path == '/api/bye':
            _leaving[0] = time.time() + GOODBYE_GRACE
            self._send(json.dumps({'ok': True}))
        else:
            self.send_error(404)

    def do_POST(self):
        _last_seen[0] = time.time()
        _leaving[0] = 0.0
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
                picked = pick_files()
                note = ''
                if not picked and sys.platform.startswith('linux') and \
                        not any(shutil.which(c[0]) for c in LINUX_PICKERS):
                    note = ('No file dialog found. Install zenity, kdialog or '
                            'yad, or pass files on the command line.')
                self._send(json.dumps({'files': picked, 'note': note}))
            elif path == '/api/settings':
                self._send(json.dumps(settings_for(body.get('printer', ''))))
            elif path == '/api/palette':
                self._send(json.dumps({'palette': palette(body.get('printer', ''))}))
            elif path == '/api/probe':
                rc, out, _ = engine(['--spectrum-probe'] + files)
                self._send(json.dumps({'colours': int((out.strip() or '0').split()[0])}))
            elif path == '/api/report':
                rargs = ['--printer', body.get('printer', ''), '--report']
                if body.get('orient'):
                    rargs.append('--orient')
                rc, out, err = engine(rargs + files)
                self._send(json.dumps({'text': out or err}))
            elif path == '/api/meshinfo':
                rc, out, err = engine(['--mesh-info'] + files)
                try:
                    self._send(json.dumps({'info': json.loads(out or '[]')}))
                except ValueError:
                    self._send(json.dumps({'info': [], 'error': err or out}))
            elif path == '/api/explain':
                args = ['--explain', body.get('term', '')]
                if body.get('printer'):
                    args += ['--printer', body['printer']]
                rc, out, err = engine(args)
                self._send(json.dumps({'text': out or err}))
            elif path == '/api/fix':
                args = ['--fix', body.get('term', '')]
                if body.get('printer'):
                    args += ['--printer', body['printer']]
                rc, out, err = engine(args + files)
                self._send(json.dumps({'text': out or err}))
            elif path == '/api/colourpreview':
                rc, out, err = engine(['--printer', body.get('printer', ''),
                                       '--colour-preview'] + files)
                self._send(json.dumps({'text': out or err}))
            elif path == '/api/reveal':
                reveal(body.get('path', ''))
                self._send(json.dumps({'ok': True}))
            elif path == '/api/ping':
                self._send(json.dumps({'ok': True}))
            elif path == '/api/bye':
                _leaving[0] = time.time() + GOODBYE_GRACE
                self._send(json.dumps({'ok': True}))
            elif path == '/api/convert':
                args = ['--printer', body.get('printer', ''),
                        '--mode', body.get('mode', 'balanced')]
                if body.get('spectrum'):
                    args.append('--spectrum')
                    if body.get('colour'):
                        args += ['--spectrum-colour', str(body['colour'])]
                if body.get('supports'):
                    args += ['--supports', str(body['supports'])]
                if body.get('units'):
                    args += ['--units', str(body['units'])]
                if body.get('up'):
                    args += ['--up', str(body['up'])]
                if body.get('orient') == 'apply':
                    args.append('--orient-apply')
                elif body.get('orient'):
                    args.append('--orient')
                for item in (body.get('sets') or []):
                    if '=' in str(item):
                        args += ['--set', str(item)]
                rc, out, err = engine(args + files)
                outs = re.findall(r'^OK -> (.+)$', out, re.M)
                self._send(json.dumps({'ok': rc == 0, 'text': out + err,
                                       'outputs': outs,
                                       'mac': sys.platform == 'darwin',
                                       'reveal_label': (
                                           'Show in Finder'
                                           if sys.platform == 'darwin' else
                                           'Show in Files'
                                           if sys.platform.startswith('linux')
                                           else 'Show in Explorer')}))
            else:
                self.send_error(404)
        except Exception as exc:
            self._send(json.dumps({'ok': False, 'text': '%s: %s'
                                   % (type(exc).__name__, exc), 'outputs': []}))


def main():
    if not FROZEN and not os.path.exists(ENGINE):
        sys.exit('engine not found next to gui.py')
    if FROZEN:
        # built as a console app so dropped files still get a console, but the
        # window makes no sense when the UI is a browser page
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0)
        except Exception:
            pass
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:%d/?t=%s' % (port, TOKEN)
    print('Prism running at', url, flush=True)
    # CI needs to drive the window without a browser appearing on the runner.
    if not os.environ.get('PRISM_NO_BROWSER'):
        webbrowser.open(url)
    try:
        while True:
            now = time.time()
            if _leaving[0] and now > _leaving[0]:
                break          # the tab was closed and did not come back
            if now - _last_seen[0] > IDLE_TIMEOUT:
                break          # nothing at all for 15 minutes
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    srv.shutdown()


if __name__ == '__main__':
    main()
