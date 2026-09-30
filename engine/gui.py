#!/usr/bin/env python3
"""Desktop front end for Prism.

Double-clicking the app with no files opens this: a small local web server plus
the default browser. A browser is used rather than Tk because it looks the same
on macOS and Windows, needs nothing installed, and can actually be styled.

Files are chosen through the NATIVE picker, not an upload, so real paths are
kept: output lands next to the original exactly as it does when files are
dropped on the icon, and a 20MB model never has to move.
"""
import http.server, json, os, re, secrets, shutil, socket, subprocess, sys, tempfile
import threading, time, webbrowser

import prismupdate
from prismversion import VERSION

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
# Measured on the MONOTONIC clock, which on macOS is mach_absolute_time and
# does not tick while the machine is asleep. The wall clock does, so the old
# version counted a closed lid as idleness and the app was reliably dead on
# waking. Sleeping is not the user going away.
#
# The length is now generous because it is no longer the main mechanism. A tab
# that is genuinely closed says goodbye explicitly, so this is only the net for
# a browser that died without a word. A frozen background tab sends no
# heartbeat either, and quitting on that is indistinguishable, from the far
# side, from quitting on somebody who simply looked at something else for a
# while. A local server costing nothing is better than a session that dies
# while you are reading.
IDLE_TIMEOUT = float(os.environ.get('PRISM_IDLE_TIMEOUT') or 8 * 3600)
# A reload fires pagehide too, so the beacon starts a countdown rather than
# quitting outright. The request the reloaded page makes cancels it.
GOODBYE_GRACE = 20.0
_last_seen = [time.monotonic()]
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


# Windows will not let a process take the foreground unless it already owns
# it, and this PowerShell is started by a local server answering an HTTP
# request, so it owns nothing. That is why the dialog opens behind the browser.
#
# Reported on 1.0.8 and again on 1.2.1, and fixed wrongly twice before CI could
# see a screen: first with an owner form at Opacity 0, which cannot take focus,
# then with an Alt keypress and a real owner, which CI proved was still not
# enough.
#
# The mechanism that does not need foreground rights is ownership. The window
# in front when Prism is clicked is the browser, and a dialog owned by a window
# is always above it in z-order. NativeWindow wraps that handle so it can be
# passed as the owner. AttachThreadInput and the raise timer stay behind it as
# a second route for the case where there is no usable foreground window.
#
# Every part is inside a try. A dialog in the wrong place beats no dialog.
WIN_PICKER_PS = (
    '$dbg = $env:PRISM_PICKER_LOG\n'
    'function Note($m) { if ($dbg) { try { Add-Content -Path $dbg -Value $m } catch { } } }\n'
    'Note "picker script started"\n'
    'try { Add-Type -AssemblyName System.Windows.Forms; Note "forms loaded" }\n'
    'catch { Note "forms FAILED: $_"; throw }\n'
    'try {\n'
    'Add-Type @\'\n'
    'using System;\n'
    'using System.Runtime.InteropServices;\n'
    'public class PrismFront {\n'
    '  [DllImport("user32.dll", CharSet=CharSet.Unicode)]\n'
    '  public static extern IntPtr FindWindow(string c, string n);\n'
    '  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);\n'
    '  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);\n'
    '  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();\n'
    '  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr p);\n'
    '  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);\n'
    '  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);\n'
    '  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();\n'
    '  public static void Raise(IntPtr h) {\n'
    '    if (h == IntPtr.Zero) return;\n'
    '    IntPtr fg = GetForegroundWindow();\n'
    '    uint other = GetWindowThreadProcessId(fg, IntPtr.Zero);\n'
    '    uint mine = GetCurrentThreadId();\n'
    '    if (other != mine) AttachThreadInput(other, mine, true);\n'
    '    ShowWindow(h, 9);\n'
    '    BringWindowToTop(h);\n'
    '    SetForegroundWindow(h);\n'
    '    if (other != mine) AttachThreadInput(other, mine, false);\n'
    '  }\n'
    '}\n'
    '\'@\n'
    'Note "native helpers compiled"\n'
    '} catch { Note "Add-Type FAILED: $_" }\n'
    '# The window in front right now is the browser Prism was clicked in. A dialog\n'
    '# OWNED by it is always above it in z-order, which needs no foreground rights\n'
    '# at all, and foreground rights are exactly what a process spawned by a local\n'
    '# server does not have.\n'
    '$fg = [IntPtr]::Zero\n'
    'try { $fg = [PrismFront]::GetForegroundWindow() } catch { Note "no GetForegroundWindow: $_" }\n'
    'Note "foreground at start: $fg"\n'
    '$d = New-Object System.Windows.Forms.OpenFileDialog\n'
    '$d.Title = "Choose models to optimise"\n'
    'try { $d.Filter = "Models|*.3mf;*.obj;*.stl|All files|*.*" } catch { }\n'
    '$d.Multiselect = $true\n'
    'try { (New-Object -ComObject WScript.Shell).SendKeys("%") } catch { }\n'
    '$owner = $null\n'
    'try {\n'
    '  if ($fg -ne [IntPtr]::Zero) {\n'
    '    $owner = New-Object System.Windows.Forms.NativeWindow\n'
    '    $owner.AssignHandle($fg)\n'
    '    Note "owning the dialog to the foreground window"\n'
    '  }\n'
    '} catch { Note "could not own to it: $_"; $owner = $null }\n'
    '$timer = $null\n'
    'try {\n'
    '  $timer = New-Object System.Windows.Forms.Timer\n'
    '  $timer.Interval = 250\n'
    '  $timer.Add_Tick({\n'
    '    try {\n'
    '      $h = [PrismFront]::FindWindow($null, "Choose models to optimise")\n'
    '      if ($h -ne [IntPtr]::Zero) {\n'
    '        [PrismFront]::Raise($h)\n'
    '        if ($env:PRISM_PICKER_LOG) {\n'
    '          Add-Content -Path $env:PRISM_PICKER_LOG -Value "raised $h"\n'
    '        }\n'
    '        $this.Stop()\n'
    '      }\n'
    '    } catch {\n'
    '      if ($env:PRISM_PICKER_LOG) {\n'
    '        Add-Content -Path $env:PRISM_PICKER_LOG -Value "tick failed: $_"\n'
    '      }\n'
    '    }\n'
    '  })\n'
    '  $timer.Start()\n'
    '  Note "raise timer started"\n'
    '} catch { Note "no timer: $_"; $timer = $null }\n'
    'if ($owner) { $res = $d.ShowDialog($owner) } else { $res = $d.ShowDialog() }\n'
    'Note "dialog closed: $res"\n'
    'if ($timer) { try { $timer.Stop() } catch { } }\n'
    'if ($owner) { try { $owner.ReleaseHandle() } catch { } }\n'
    'if ($res -eq [System.Windows.Forms.DialogResult]::OK) {\n'
    '  $d.FileNames -join [Environment]::NewLine\n'
    '}\n'
)


def pick_files():
    """Native multi-select file dialog.

    Returns (paths, note). A note is something to tell the person: this used to
    return an empty list whether they cancelled or the dialog never opened, so
    a picker that died reported exactly what a cancel reports, and "nothing
    happens when I click" was the only symptom anyone could describe."""
    if sys.platform.startswith('linux'):
        for cmd in LINUX_PICKERS:
            if not shutil.which(cmd[0]):
                continue
            p = subprocess.run(cmd, capture_output=True, text=True)
            return ([l for l in p.stdout.replace('|', '\n').splitlines()
                     if l.strip() and os.path.exists(l.strip())], None)
        return [], ('No file dialog found. Install zenity, kdialog or yad, or '
                    'pass files on the command line.')
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
        if p.returncode and not p.stdout.strip():
            return [], 'The file dialog did not open: %s' % (
                (p.stderr or '').strip().splitlines()[-1:] or ['unknown'])[0][:120]
        return [l for l in p.stdout.splitlines() if l.strip()], None

    # Windows. Run a SCRIPT FILE rather than -Command: everything after
    # -Command is re-joined and re-parsed by PowerShell, so a filter carrying
    # semicolons, parentheses and a pipe is at the mercy of that. A file has no
    # quoting layer at all.
    #
    # The filter is also set inside a try: OpenFileDialog validates it on
    # assignment and THROWS on anything it dislikes, which would kill the
    # script before ShowDialog and open no dialog whatsoever. An unfiltered
    # dialog is a far better failure than no dialog.
    script = WIN_PICKER_PS
    p = None
    fh = tempfile.NamedTemporaryFile('w', suffix='.ps1', delete=False,
                                     encoding='utf-8')
    try:
        fh.write(script); fh.close()
        p = subprocess.run(['powershell', '-NoProfile', '-STA',
                            '-ExecutionPolicy', 'Bypass', '-File', fh.name],
                           capture_output=True, text=True)
    except OSError as exc:
        # PowerShell missing from PATH, blocked by policy, or the script
        # refused. Read after the finally below, p would be unbound and the
        # traceback would name the wrong thing entirely.
        return [], 'The file dialog could not be started: %s' % exc
    finally:
        try:
            os.unlink(fh.name)
        except OSError:
            pass
    files = [l.strip() for l in p.stdout.splitlines() if l.strip()]
    if not files and (p.returncode or (p.stderr or '').strip()):
        err = ((p.stderr or '').strip().splitlines() or ['no output'])[-1]
        return [], 'The file dialog did not open: %s' % err[:140]
    return files, None


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
 ('idle_temperature', 'Idle nozzle temperature', 'tempslider', 'Idle temperature',
  'What a nozzle sits at while another one is printing. Only the tools this '
  'print actually uses are heated at all, so this is about the ones waiting '
  'their turn. Low and they are inert, but every tool change then waits for a '
  'nozzle to climb back to printing temperature, which on a print with hundreds '
  'of changes is a great deal of waiting. High and the changes are quick, but a '
  'molten nozzle weeps between them and spends the whole print hot rather than '
  'only while it works, which on a long job is how heat creep and clogs start. '
  'Zero switches it off and falls back to the printer profile\'s own '
  'standby difference.'),
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


# Which plate's temperature key to read, shared with the engine so the panel
# and the converted file can never disagree about which number is the bed.
def _bed_plates():
    try:
        with open(os.path.join(HERE, 'data', 'bed-plates.json'), encoding='utf-8') as fh:
            d = json.load(fh)
        return d['plates'], [tuple(x) for x in d['fallback']]
    except Exception as e:
        sys.stderr.write('prism: BROKEN BUILD: bed-plates.json did not load '
                         '(%s: %s)\n' % (type(e).__name__, e))
        return {}, []


BED_TEMP_KEYS, BED_FALLBACK = _bed_plates()


def _num(v, fallback=0):
    try:
        return int(float(_first(v)))
    except (TypeError, ValueError):
        return fallback


def _first(v):
    """3MF settings are lists of one. Unwrap without assuming which."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


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
        row = {'key': k, 'label': label, 'kind': kind,
               'slicer': slicer_name, 'help': helptext,
               'effective': defaults.get(k, cur),
               'prism': defaults.get(k),
               'options': sorted(enums.get(k, []))}
        if kind == 'tempslider':
            # The ceiling is this printer's own printing temperature: idling
            # hotter than you print is not a thing anyone wants, and a slider
            # that offers it invites the question.
            hot = _num(_first(tpl.get('nozzle_temperature')), 250)
            row['min'], row['max'], row['step'] = 0, hot, 5
            # And say whether it will do anything here at all. The setting is
            # inert unless ooze prevention is on, which it is on 7 of the 24
            # machines, so on the others this control would silently do nothing
            # and look broken rather than inapplicable.
            if str(_first(tpl.get('ooze_prevention'))) == '1':
                delta = _num(tpl.get('standby_temperature_delta'), 0)
                row['help'] += (' On this printer the standby difference is %d, '
                                'so leaving this at zero parks an idle nozzle '
                                'at %dC.' % (delta, hot + delta))
            else:
                row['help'] += (' Note that this printer has ooze prevention '
                                'switched off, so it will ignore this value '
                                'until that is turned on in the slicer.')
                row['inert'] = True
        rows.append(row)

    # Bed temperature is the one setting whose key is not fixed: it lives
    # under whichever plate the profile selects, so the row is resolved here
    # rather than listed in QUICK. Without it there is no temperature control
    # in the panel at all, and the only way to change the bed was --set.
    plate = str(_first(tpl.get('curr_bed_type')) or '')
    bk = BED_TEMP_KEYS.get(plate)
    def _t(k):
        try:
            return float(_first(tpl.get(k)))
        except (TypeError, ValueError):
            return 0.0
    cur = None
    if bk and bk in tpl:
        cur = str(_first(tpl[bk]))
        if not _t(bk):
            # This profile sets nothing for the plate it selects. The engine
            # fills it from a sibling plate and keeps the plate name, so show
            # the number the converted file will actually carry.
            for _b2, k2 in BED_FALLBACK:
                if _t(k2):
                    cur = str(_first(tpl[k2]))
                    break
    if cur is not None:
        # The panel badges a Prism override, so the explanation must not also
        # claim the number came from the printer maker. It says which it is.
        mine = defaults.get(bk)
        if mine is not None and str(mine) != str(_first(tpl.get(bk))):
            provenance = ('Prism sets %sC here, in place of the %s\'s own %sC, '
                          'which runs warm enough to splay the bottom few '
                          'layers outwards. Type %s to put it back.'
                          % (mine, d.get('label', 'printer'),
                             _first(tpl.get(bk)), _first(tpl.get(bk))))
        else:
            provenance = 'This is your printer\'s own value, left as it ships.'
        rows.append({
            'key': bk, 'label': 'Bed temperature', 'kind': 'num',
            'slicer': plate + ' temperature',
            'help': 'How hot the print bed runs, in Celsius. It is what holds '
                    'the first layer down: too cool and the part lets go part '
                    'way through, too hot and the bottom few layers soften and '
                    'splay out into an elephant foot. Printer makers disagree '
                    'about the right number for the same plastic, so PLA is '
                    'run anywhere between 50 and 65 depending on the machine. '
                    + provenance +
                    ' If your parts are sticking and their bottom edge is '
                    'square, you have no reason to change it. This applies to '
                    'the ' + plate + ', which is the surface this profile '
                    'selects, and the first layer moves with it.',
            'effective': defaults.get(bk, cur),
            'prism': defaults.get(bk),
            'options': []})
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
/* The band is every colour a Snapmaker U1 can print: 4 loaded filaments and
   18 blends of them, hard stops so nothing between them is implied. The dark
   wash over it is what makes white type legible across yellows as well as
   blues; without it the text vanishes over a third of the width. */
.masthead{position:relative;overflow:hidden;border-radius:14px;padding:22px 24px;
 margin:0 0 20px;display:flex;align-items:center;gap:16px;
 background-image:linear-gradient(90deg,rgba(0,0,0,.72) 0%,rgba(0,0,0,.66) 46%,rgba(0,0,0,.30) 78%,rgba(0,0,0,.14) 100%),linear-gradient(90deg, #E99066 0.0%, #E99066 4.55%, #F2BC54 4.55%, #F2BC54 9.09%, #F9ED3D 9.09%, #F9ED3D 13.64%, #D8DF50 13.64%, #D8DF50 18.18%, #BCCC65 18.18%, #BCCC65 22.73%, #A6B681 22.73%, #A6B681 27.27%, #A9EC57 27.27%, #A9EC57 31.82%, #68E27C 31.82%, #68E27C 36.36%, #3ACEB3 36.36%, #3ACEB3 40.91%, #49A3CA 40.91%, #49A3CA 45.45%, #29A7E2 45.45%, #29A7E2 50.0%, #08ABFB 50.0%, #08ABFB 54.55%, #6C9DB7 54.55%, #6C9DB7 59.09%, #9199A4 59.09%, #9199A4 63.64%, #417CD7 63.64%, #417CD7 68.18%, #735DB9 68.18%, #735DB9 72.73%, #A747A0 72.73%, #A747A0 77.27%, #A6789D 77.27%, #A6789D 81.82%, #B95F97 81.82%, #B95F97 86.36%, #CA4A93 86.36%, #CA4A93 90.91%, #D93B90 90.91%, #D93B90 95.45%, #E3647B 95.45%, #E3647B 100.0%);
 background-size:cover;box-shadow:0 1px 3px rgba(0,0,0,.2)}
.masthead img{width:56px;height:56px;flex:0 0 56px;border-radius:13px;
 box-shadow:0 2px 8px rgba(0,0,0,.35)}
.masthead .t{min-width:0}
.masthead h1{color:#fff;font-weight:700;text-shadow:0 1px 3px rgba(0,0,0,.5)}
.masthead .ver{color:#fff;opacity:.92;font-size:12px;margin:6px 0 0;
 text-shadow:0 1px 3px rgba(0,0,0,.55)}
.masthead .ver button{background:rgba(255,255,255,.16);color:#fff;border:0;
 border-radius:7px;padding:3px 9px;font:inherit;cursor:pointer}
.masthead .ver button:hover{background:rgba(255,255,255,.28)}
#upd{background:var(--accent);color:var(--accentink);padding:11px 14px;
 border-radius:11px;margin:0 0 14px;font-size:14px;display:flex;gap:12px;
 align-items:center;flex-wrap:wrap}
#upd a{color:inherit;font-weight:700}
#upd button{margin-left:auto;background:rgba(0,0,0,.18);color:inherit;border:0;
 border-radius:7px;padding:4px 10px;font:inherit;cursor:pointer}
.masthead .sub{color:#fff;font-weight:600;margin:2px 0 0;text-shadow:0 1px 3px rgba(0,0,0,.55)}
@media (max-width:460px){.masthead{padding:16px}
 .masthead img{width:44px;height:44px;flex-basis:44px}}
.masthead img{width:56px;height:56px;flex:0 0 56px;border-radius:13px;
 box-shadow:0 1px 3px rgba(0,0,0,.18)}
.masthead .t{min-width:0}
@media (max-width:460px){.masthead img{width:44px;height:44px;flex-basis:44px}}
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
<div id="upd" hidden></div>
<div class="masthead"><img src="data:image/png;base64,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" alt="" width="56" height="56">
<div class="t"><h1>Prism</h1>
<p class="sub">Retarget any 3MF. Blend any colour.  &middot;  24 printers, four slicer dialects, Full Spectrum on the Snapmaker&nbsp;U1.</p>
<p class="ver">Version __VERSION__ &middot; <button id="updbtn" type="button">Check for updates</button> <span id="updsay"></span></p></div></div>

<div class="card" id="c1"><div class="step"><div class="num">1</div><h2>Choose files</h2></div>
<div class="row"><button id="pick">Choose files…</button>
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
<pre id="cpout" hidden></pre>
<label for="fsstep">Blend detail</label>
<select id="fsstep">
 <option value="">Finest: a full colour cycle per normal layer (about 2x the time)</option>
 <option value="0.14">Finer: about 1.4x the time, slight banding on flat faces</option>
 <option value="0.16">Coarser: about 1.25x the time, more banding on flat faces</option>
 <option value="off">Normal layers: no extra time, flat faces show one filament</option>
</select>
<p class="hint">Blending alternates thin layers until your eye reads them as one
colour, so a full cycle has to fit inside one normal layer. That is what doubles
the time. Coarser bands print faster and show more on flat tops; curved and
textured surfaces hide them well.</p>
</div></div>

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
/* A script error during setup binds no handlers, and every control then does
   nothing with no sign of why. That is unreportable: the only symptom available
   is "nothing happens". Surface it instead, at the top of the page. */
window.addEventListener('error',e=>{showFault((e&&e.message)||'script error');});
window.addEventListener('unhandledrejection',e=>{
  showFault('unhandled: '+((e&&e.reason&&e.reason.message)||e.reason||'?'));});
function showFault(msg){
  try{
    let d=document.getElementById('fault');
    if(!d){d=document.createElement('div');d.id='fault';
      d.style.cssText='background:#b3261e;color:#fff;padding:10px 14px;'+
        'border-radius:10px;margin:0 0 14px;font:13px/1.45 ui-monospace,monospace;'+
        'white-space:pre-wrap;word-break:break-word';
      const w=document.querySelector('.wrap');
      if(w)w.insertBefore(d,w.firstChild); else document.body.appendChild(d);}
    d.textContent='Prism hit a problem in the page:\n'+msg+
      '\n\nPlease report this at github.com/4bsxhwr68n-debug/prism/issues';
  }catch(_){}
}

const api=(p,b)=>fetch(p+'?t='+T,{method:b?'POST':'GET',headers:{'Content-Type':'application/json'},
  body:b?JSON.stringify(b):null}).then(r=>r.json()).then(r=>{
  /* A failure anywhere in the dispatch answers {ok:false,text:...} and none
     of the keys the caller expects. Every caller then died on the missing key
     and reported "cannot read properties of undefined", which named the line
     that noticed rather than the thing that broke: issue #2 was a picker that
     threw on Windows, reported as a JavaScript error about .length. Raise the
     server's own words so the banner shows the actual cause. */
  if(r&&r.ok===false&&r.text)throw new Error(r.text);
  return r;});
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

/* A page whose engine has gone looks completely normal and every control does
   nothing, which is the same unreportable symptom as a script error. Two
   misses in a row is the engine being gone rather than one dropped request. */
let missed=0;
const beat=()=>api('/api/ping').then(()=>{
  missed=0;
  const d=document.getElementById('fault');
  if(d&&d.dataset.gone){d.remove();}
}).catch(()=>{
  if(++missed<2)return;
  showFault('The Prism engine is no longer running, so nothing on this page '
   +'will work. This window can be closed. Start Prism again to carry on.');
  const d=document.getElementById('fault'); if(d)d.dataset.gone='1';});
setInterval(beat,8000);
/* A hidden tab's timers are throttled and eventually frozen, so beat again the
   moment the page is looked at rather than waiting for the next tick. */
document.addEventListener('visibilitychange',()=>{if(!document.hidden)beat();});
window.addEventListener('focus',beat);
/* Closing the tab should quit the app promptly instead of leaving it running.
   pagehide also fires on a reload, so this only starts a countdown, and the
   reloaded page cancels it with its first request. */
window.addEventListener('pagehide',e=>{
 /* pagehide also fires when the page is only being parked in the back/forward
    cache, and a parked page can be restored. Saying goodbye then quits the app
    behind a tab that is still perfectly alive. e.persisted tells the two apart:
    true means parked, false means actually going. */
 if(e && e.persisted)return;
 try{navigator.sendBeacon('/api/bye?t='+T);}catch(err){}});
/* Restored from that cache: announce we are back, in case a goodbye did go. */
window.addEventListener('pageshow',e=>{if(e&&e.persisted)beat();});

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
    : f.kind==='tempslider'
    /* Zero is not a temperature here, it is "off", so the readout says so
       rather than showing a number the printer will never go to. */
    ? `<div class="sl"><input type="range" min="${f.min}" max="${f.max}" step="${f.step}"
         value="${f.effective}" data-k="${f.key}" data-def="${f.effective}" data-temp="1">
       <output>${Number(f.effective)?f.effective+'&deg;C':'off'}</output></div>`
    : f.kind==='enum'&&f.options.length
    ? `<select data-k="${f.key}"><option value="">${f.effective}</option>`+
      f.options.map(o=>`<option value="${o}">${o}</option>`).join('')+`</select>`
    : `<input data-k="${f.key}" placeholder="${f.effective}">`;
   return `<div class="fld"><label class="lbl">${f.label}${mark}`+
    `<span class="i" data-h="h${i}" title="What does this do?">i</span></label>`+
    `${ctl}<div class="hlp" id="h${i}">`+
    `<b>In the slicer: ${f.slicer}</b><br>${f.help}</div></div>`;
  }).join('');}).catch(e=>{
   /* Without this the grid was simply emptied and the panel looked as though
      the settings had been removed from the app. Say which read failed. */
   wait.hidden=false;
   wait.textContent='These settings could not be read: '+((e&&e.message)||e);
   document.getElementById('advgrid').innerHTML='';
   showFault('settings: '+((e&&e.message)||e));});}

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
  if(!o)return;
  /* A temperature slider is not a percentage, and its zero is "off" rather
     than nought degrees. The readout has to say which it is. */
  if(e.target.dataset.temp)o.innerHTML=Number(e.target.value)?e.target.value+'&deg;C':'off';
  else o.textContent=e.target.value+'%';}};
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

/* Update check. Passive by design: it asks GitHub what the newest release is
   and, if it is newer than this build, says so and links to it. It downloads
   nothing and replaces nothing. A machine that cannot reach GitHub is told
   nothing on launch, because "I could not check" is not worth a banner. */
function showUpdate(r,loud){
 const bar=document.getElementById('upd'), say=document.getElementById('updsay');
 if(r&&r.newer){
  bar.innerHTML='<span>Prism <b>'+r.latest+'</b> is available. You are running '+
   r.current+'. <a href="'+r.url+'" target="_blank" rel="noopener">See what changed and download it</a></span>'+
   '<button type="button" id="upddis">Not now</button>';
  bar.hidden=false;
  document.getElementById('upddis').onclick=()=>{bar.hidden=true;
   try{localStorage.setItem('prismSkip',r.latest);}catch(e){}};
  say.textContent='';
  return;}
 bar.hidden=true;
 /* Only the button says "you are up to date". On launch, silence. */
 if(loud)say.textContent=r&&r.checked?'up to date':(r&&r.note)||'could not check';}

document.getElementById('updbtn').onclick=()=>{
 const say=document.getElementById('updsay');
 say.textContent='checking…';
 api('/api/update',{force:true}).then(r=>showUpdate(r,true))
  .catch(e=>{say.textContent='could not check';});};

api('/api/update',{}).then(r=>{
 /* A version already dismissed stays dismissed until a newer one appears. */
 let skip=null; try{skip=localStorage.getItem('prismSkip');}catch(e){}
 if(r&&r.newer&&skip===r.latest)return;
 showUpdate(r,false);}).catch(e=>{});

document.getElementById('pick').onclick=()=>api('/api/pick',{}).then(r=>{
 if(r.note){document.getElementById('filehint').textContent=r.note;return;}
 if(!(r.files||[]).length)return; S.files=r.files;
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
    b.classList.add('sel');refresh();analyse();});
  body.querySelectorAll('[data-up]').forEach(b=>b.onclick=()=>{
    S.up=b.dataset.up;
    body.querySelectorAll('[data-up]').forEach(x=>x.classList.remove('sel'));
    b.classList.add('sel');refresh();analyse();});
 }).catch(()=>{body.textContent='could not measure it';});
}

function analyse(){if(!S.files.length||!S.printer)return;
 document.getElementById('c3').classList.remove('off');
 document.getElementById('report').innerHTML='<span class="spin"></span> analysing…';
 api('/api/report',{printer:S.printer,files:S.files,units:S.units||null,up:S.up||null,
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
   spectrumStep:(S.spectrum?(document.getElementById('fsstep').value||null):null),
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
        _last_seen[0] = time.monotonic()
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
            page = page.replace('__VERSION__', VERSION)
            self._send(page, 'text/html; charset=utf-8')
        elif path == '/api/printers':
            self._send(json.dumps({'printers': printers()}))
        elif path == '/api/ping':
            self._send(json.dumps({'ok': True}))
        elif path == '/api/bye':
            _leaving[0] = time.monotonic() + GOODBYE_GRACE
            self._send(json.dumps({'ok': True}))
        else:
            self.send_error(404)

    def do_POST(self):
        _last_seen[0] = time.monotonic()
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
                # The note is whatever the picker could not do, which the page
                # shows instead of leaving the button looking inert. Caught
                # here rather than by the dispatch below, because that answers
                # without a 'files' key at all and the page reported the
                # missing key instead of the reason (issue #2).
                try:
                    picked, note = pick_files()
                except Exception as exc:
                    picked = []
                    note = ('The file dialog could not run: %s: %s'
                            % (type(exc).__name__, exc))
                self._send(json.dumps({'files': picked, 'note': note or ''}))
            elif path == '/api/update':
                # Asked once on launch and again when the button is pressed.
                # Never raises and never blocks anything: a machine that cannot
                # reach GitHub is told nothing, which is the correct amount.
                self._send(json.dumps(
                    prismupdate.check(force=bool(body.get('force')))))
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
                if body.get('units'):
                    rargs += ['--units', str(body['units'])]
                if body.get('up'):
                    rargs += ['--up', str(body['up'])]
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
                _leaving[0] = time.monotonic() + GOODBYE_GRACE
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
                if body.get('spectrumStep'):
                    args += ['--spectrum-step', str(body['spectrumStep'])]
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
    why = 'stopped'
    try:
        while True:
            now = time.monotonic()
            if _leaving[0] and now > _leaving[0]:
                why = 'the window was closed'
                break
            if now - _last_seen[0] > IDLE_TIMEOUT:
                why = ('nothing was heard from the window for %g hours'
                       % (IDLE_TIMEOUT / 3600.0))
                break
            time.sleep(1)
    except KeyboardInterrupt:
        why = 'interrupted'
    # Say why. A report of "it stops after a while" could not be told apart
    # from a crash without this, and the reason is the whole diagnosis.
    print('Prism is stopping: %s.' % why, flush=True)
    srv.shutdown()


if __name__ == '__main__':
    main()
