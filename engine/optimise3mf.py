#!/usr/bin/env python3
"""Prism: retarget any 3mf project for 24 printers across
Creality / Snapmaker / Bambu Lab / Prusa / Elegoo / Voron / Qidi / Sovol /
Anycubic / Flashforge, with model-aware quality/speed planning.

Usage:
  python3 optimise3mf.py --list
  python3 optimise3mf.py --printer <key> [--report] [--mode speed|balanced|quality]
                         [--single [#RRGGBB]] [--spectrum] [--dome off|H]
                         [--no-analyse] [--out PATH] file.3mf [more.3mf ...]
  python3 optimise3mf.py --interactive file.3mf [...]   (console prompts)

The tool analyses each model's geometry first (bed fit, overhangs vs the
support setting, rounded-top "stair ring" risk, large flat tops) and derives
three concrete plans:
  speed     0.28mm layers, dome objects 0.16          (~0.7x balanced time)
  balanced  0.20mm layers, dome objects 0.12          (1x)
  quality   0.16mm layers, dome objects 0.08, ironing on large flat tops
                                                      (~1.3-1.6x)
A source project authored finer than the mode's layer height keeps its finer
value (we never coarsen below the designer's choice except in speed mode).
Geometry is never modified, hash-verified on every run.
"""
import argparse, hashlib, json, math, os, re, shutil, sys, tempfile, zipfile
import mixer
import xml.etree.ElementTree as ET

__version__ = '2.0'
# _MEIPASS is where PyInstaller unpacks the bundle; falls back to the script
# directory when running from source.
TOOL_DIR = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(TOOL_DIR, 'data')

CARRY_KEYS = ['wall_loops', 'wall_generator',
              'sparse_infill_density', 'sparse_infill_pattern',
              'top_shell_layers', 'bottom_shell_layers',
              'enable_support', 'support_type', 'support_style',
              'support_threshold_angle', 'support_on_build_plate_only',
              'brim_type', 'brim_width', 'seam_position', 'ironing_type']
ENUM_KEYS = {'sparse_infill_pattern', 'brim_type', 'support_type',
             'support_style', 'seam_position', 'wall_generator', 'ironing_type'}
META_SKIP = {'name', 'inherits', 'from', 'instantiation', 'setting_id',
             'filament_id', 'filament_settings_id', 'compatible_printers',
             'compatible_printers_condition', 'compatible_prints',
             'compatible_prints_condition', 'version', 'is_custom_defined'}
ANALYSE_BUDGET_BYTES = 60_000_000

# The slicer's own max_bridge_length on these machines is 10mm, so a ceiling
# narrower than that is expected to bridge rather than need holding up.
BRIDGE_LIMIT = 10.0
BRIDGE_BAND = 0.5        # group ceilings into Z bands this tall before measuring
SUPPORT_MIN_AREA = 40.0  # mm2 of unbridgeable ceiling before supports earn their place

MODES = {  # global layer height, dome-object layer height
    'speed':    {'lh': 0.28, 'dome': 0.16, 'time': '~0.7x'},
    'balanced': {'lh': 0.20, 'dome': 0.12, 'time': '1x'},
    'quality':  {'lh': 0.16, 'dome': 0.08, 'time': '~1.3-1.6x'},
}


def load_index():
    with open(os.path.join(DATA, 'index.json'), encoding='utf-8') as f:
        return json.load(f)


def load_printer(key):
    p = os.path.join(DATA, 'printers', key + '.json')
    if not os.path.exists(p):
        sys.exit(f"unknown printer '{key}'. Run with --list to see keys")
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def norm_type(t):
    t = (t or 'PLA').upper()
    if 'SILK' in t: return 'PLA-SILK'
    for k in ('PLA-CF', 'PETG-CF', 'PETG', 'PLA', 'ABS', 'ASA', 'TPU',
              'PVA', 'PET', 'PA', 'PP'):
        if t.startswith(k): return k
    return t


# ------------------------- geometry analysis ---------------------------

IDENTITY = '1 0 0 0 1 0 0 0 1 0 0 0'


def plate_items(src):
    """(objectid, transform) for every item on the plate.

    Parsed per tag rather than with one regex over the pair, because BOTH of
    those assumptions were wrong: `transform` is optional in 3MF and defaults
    to identity, and attributes may appear in any order. A file written as
    `<item objectid="8" printable="1"/>` was skipped entirely, so the mesh was
    never analysed and the report said "clean geometry" having looked at
    nothing."""
    out = []
    for tag in re.findall(r'<item\b[^>]*?/?>', src):
        oid = re.search(r'\bobjectid="([^"]+)"', tag)
        if not oid:
            continue
        tr = re.search(r'\btransform="([^"]+)"', tag)
        out.append((oid.group(1), tr.group(1) if tr else IDENTITY))
    return out


def _parse_model(path):
    """Objects as (vertices, triangle index triples) plus their components.

    Indices, not coordinates: edge topology needs to know which vertices are
    the SAME vertex, and comparing floats cannot tell a shared vertex from two
    that happen to coincide."""
    objs, comps = {}, {}
    cur, V, T = None, None, None
    for ev, el in ET.iterparse(path, events=('start', 'end')):
        tag = el.tag.split('}')[-1]
        if ev == 'start' and tag == 'object':
            cur = el.get('id'); V, T = [], []; comps.setdefault(cur, [])
        elif ev == 'end':
            if tag == 'vertex' and cur is not None:
                V.append((float(el.get('x')), float(el.get('y')), float(el.get('z'))))
            elif tag == 'triangle' and cur is not None:
                T.append((int(el.get('v1')), int(el.get('v2')), int(el.get('v3'))))
            elif tag == 'component' and cur is not None:
                p = [v for k, v in el.attrib.items() if k.endswith('path')]
                comps[cur].append((p[0] if p else None, el.get('objectid'),
                                   el.get('transform') or '1 0 0 0 1 0 0 0 1 0 0 0'))
            elif tag == 'object':
                objs[cur] = (V, T); cur = None; el.clear()
            elif tag in ('vertex', 'triangle'):
                el.clear()
    return objs, comps


def parse_meshes(tmp):
    root_path = os.path.join(tmp, '3D', '3dmodel.model')
    if not os.path.exists(root_path):
        return {}
    parse = _parse_model

    def mat(s): return [float(x) for x in s.split()]

    def apply(m, p):
        x, y, z = p
        return (m[0]*x + m[3]*y + m[6]*z + m[9],
                m[1]*x + m[4]*y + m[7]*z + m[10],
                m[2]*x + m[5]*y + m[8]*z + m[11])

    robjs, rcomps = parse(root_path)
    cache = {}
    s = open(root_path, encoding='utf-8').read()
    result = {}
    for objid, tr in plate_items(s):
        m = mat(tr); tris = []
        srcs = rcomps.get(objid) or [(None, objid, '1 0 0 0 1 0 0 0 1 0 0 0')]
        for (p, oid, ctr) in srcs:
            if p:
                if p not in cache:
                    cache[p] = parse(os.path.join(tmp, p.lstrip('/')))
                o2, _ = cache[p]
            else:
                o2 = robjs
            if oid not in o2: continue
            V, T = o2[oid]
            mm = mat(ctr)
            for t in T:
                tris.append([apply(m, apply(mm, V[i])) for i in t])
        if tris: result[objid] = tris
    return result


# Mesh health. Reported, never repaired: Prism's promise is that geometry is
# untouched, and a tool that silently "fixes" a mesh cannot make that promise.
# The point is to say what is wrong BEFORE eight hours of printing, because
# these faults are invisible in a preview and only show up as a failed print.
WELD = 5                  # decimal places: coincident vertices are one vertex
HOLE_REPORT = 1           # any boundary edge at all is worth a word


def _weld(V):
    """Map each vertex to a canonical index, so two vertices at the same point
    count as one. Exporters routinely emit a separate vertex per triangle
    corner, which would otherwise make every edge look like a hole."""
    canon, out = {}, []
    for v in V:
        k = (round(v[0], WELD), round(v[1], WELD), round(v[2], WELD))
        out.append(canon.setdefault(k, len(canon)))
    return out, len(canon)


def mesh_health(V, T):
    """Topology faults in one mesh, from its own indices.

    A closed surface has every edge shared by exactly two triangles. One means
    a hole. Three or more means the surface folds back on itself and no slicer
    can tell inside from outside there."""
    idx, nverts = _weld(V)
    edges = {}
    degenerate = 0
    parent = list(range(nverts))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb

    used = set()
    for t in T:
        a, b, c = idx[t[0]], idx[t[1]], idx[t[2]]
        if a == b or b == c or a == c:
            degenerate += 1               # collapsed to a line or a point
            continue
        used.update((a, b, c))
        union(a, b); union(b, c)
        for e in ((a, b), (b, c), (c, a)):
            edges[(min(e), max(e))] = edges.get((min(e), max(e)), 0) + 1

    boundary = sum(1 for n in edges.values() if n == 1)
    nonmanifold = sum(1 for n in edges.values() if n > 2)
    shells = len({find(v) for v in used})
    return {'triangles': len(T), 'boundary': boundary,
            'nonmanifold': nonmanifold, 'shells': shells,
            'degenerate': degenerate,
            'watertight': boundary == 0 and nonmanifold == 0}


def health_lines(h):
    """What to tell someone about one object's mesh.

    Worded as risk and proportion, never as a verdict. Plenty of imperfect
    meshes print perfectly, and a tool that cries wolf about every model gets
    ignored on the one that matters. Separate shells are reported as a fact,
    not a fault: a model of loose parts is supposed to have them."""
    out = []
    n = max(1, h['triangles'])
    if h['nonmanifold']:
        scale = 'a few' if h['nonmanifold'] * 10000 < n else 'widespread'
        out.append(f"{h['nonmanifold']} edge(s) where the surface meets itself "
                   f"({scale}), so inside and outside are ambiguous there")
    if h['boundary']:
        scale = 'a pinhole' if h['boundary'] * 10000 < n else 'a real gap'
        out.append(f"{h['boundary']} open edge(s), so the surface is not closed "
                   f"({scale}); slicers fill this in by guessing")
    if h['degenerate']:
        out.append(f"{h['degenerate']} triangle(s) with no area, usually harmless")
    if h['shells'] > 1:
        out.append(f"{h['shells']} separate pieces, which is normal for a "
                   "multi-part model and a problem only if you expected one")
    return out


def parse_health(tmp):
    """Mesh health per item on the plate, mirroring parse_meshes' grouping so
    the numbers line up with the objects a person can see. Read from the SOURCE
    mesh, before transforms, because moving or turning a part cannot open a
    hole in it."""
    root_path = os.path.join(tmp, '3D', '3dmodel.model')
    if not os.path.exists(root_path):
        return {}
    robjs, rcomps = _parse_model(root_path)
    cache, out = {}, {}
    src = open(root_path, encoding='utf-8').read()
    for objid, _tr in plate_items(src):
        agg = {'triangles': 0, 'boundary': 0, 'nonmanifold': 0,
               'shells': 0, 'degenerate': 0}
        for (pth, oid, _ctr) in (rcomps.get(objid) or [(None, objid, '')]):
            if pth:
                if pth not in cache:
                    cache[pth] = _parse_model(os.path.join(tmp, pth.lstrip('/')))
                o2, _ = cache[pth]
            else:
                o2 = robjs
            if oid not in o2:
                continue
            V, T = o2[oid]
            if not T:
                continue
            h = mesh_health(V, T)
            for k in agg:
                agg[k] += h[k]
        if agg['triangles']:
            agg['watertight'] = agg['boundary'] == 0 and agg['nonmanifold'] == 0
            out[objid] = agg
    return out


def analyse_object(tris, bed):
    xs = [p[0] for t in tris for p in t]; ys = [p[1] for t in tris for p in t]
    zs = [p[2] for t in tris for p in t]
    zmin = min(zs)
    dims = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - zmin)
    oversize = dims[0] > bed[0] or dims[1] > bed[1] or dims[2] > bed[2]
    ceiling = steep = moderate = 0.0   # downward area by slope from horizontal
    ceil_bands = {}                    # z band -> [minx,maxx,miny,maxy,area]
    over_z_lo, over_z_hi = 1e9, -1e9
    down_flat = 0.0          # unsupported near-flat ceilings (needs supports?)
    up_shallow = 0.0         # 3-30 deg from horizontal: stair-ring zone
    up_flat = 0.0            # <=3 deg true flat tops (ironing candidates)
    up_total = 0.0
    for t in tris:
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
        ux, uy, uz = bx-ax, by-ay, bz-az
        vx, vy, vz = cx-ax, cy-ay, cz-az
        nx = uy*vz - uz*vy; ny = uz*vx - ux*vz; nz = ux*vy - uy*vx
        L = math.sqrt(nx*nx + ny*ny + nz*nz)
        if L == 0: continue
        area = L/2; nzn = nz/L
        if nzn > 0:
            ang = math.degrees(math.acos(min(1.0, nzn)))
            up_total += area
            lowz = min(p[2] for p in t)
            if ang <= 3.0 and lowz > zmin + 0.5:
                up_flat += area
            elif 3.0 < ang < 30.0 and lowz > zmin + 1.0:
                up_shallow += area
            continue
        # Downward face. `ang` is the surface's slope from horizontal, the same
        # convention the slicer's support_threshold_angle uses: 0 is a flat
        # ceiling, 90 is a vertical wall.
        ang = math.degrees(math.acos(min(1.0, -nzn)))
        lz = min(p[2] for p in t)
        if lz <= zmin + 0.25: continue          # sitting on the plate
        if ang < 20: down_flat += area
        if ang < 10:
            ceiling += area
            band = round(lz / BRIDGE_BAND) * BRIDGE_BAND
            bx = ceil_bands.setdefault(band, [1e9, -1e9, 1e9, -1e9, 0.0])
            for (px, py, _) in t:
                bx[0] = min(bx[0], px); bx[1] = max(bx[1], px)
                bx[2] = min(bx[2], py); bx[3] = max(bx[3], py)
            bx[4] += area
        elif ang < 30:
            steep += area
        elif ang < 55:
            moderate += area
        if ang < 30:
            over_z_hi = max(over_z_hi, max(p[2] for p in t))
            over_z_lo = min(over_z_lo, lz)

    # Span each ceiling has to cross. Grouped into thin Z bands and measured as
    # the SHORTER side of the band's bounding box, because a long thin ledge
    # bridges across its narrow dimension. This is an estimate from the mesh,
    # not the slicer's own per-layer bridge detection.
    spans = sorted((min(b[1] - b[0], b[3] - b[2]), b[4])
                   for b in ceil_bands.values() if b[4] > 1.0)
    max_span = max((sp for sp, _ in spans), default=0.0)
    bridgeable = sum(a for sp, a in spans if sp <= BRIDGE_LIMIT)
    needs_span = sum(a for sp, a in spans if sp > BRIDGE_LIMIT)

    dome = up_shallow > 500 and up_total > 0 and up_shallow / up_total > 0.10
    return {'dims': dims, 'oversize': oversize, 'down_flat': down_flat,
            'up_shallow': up_shallow, 'up_flat': up_flat, 'dome': dome,
            'ceiling': ceiling, 'steep': steep, 'moderate': moderate,
            'max_span': max_span, 'bridgeable': bridgeable,
            'needs_span': needs_span,
            'over_z_lo': (0.0 if over_z_lo > 1e8 else over_z_lo - zmin),
            'over_z_hi': (0.0 if over_z_hi < -1e8 else over_z_hi - zmin),
            'height': dims[2]}


def analyse_file(tmp, bed, skip):
    mesh_bytes = sum(os.path.getsize(os.path.join(r, f))
                     for r, _, fs in os.walk(os.path.join(tmp, '3D')) for f in fs) \
        if os.path.isdir(os.path.join(tmp, '3D')) else 0
    if skip or mesh_bytes > ANALYSE_BUDGET_BYTES:
        return None, mesh_bytes
    metrics = {objid: analyse_object(tris, bed)
               for objid, tris in parse_meshes(tmp).items()}
    for objid, h in parse_health(tmp).items():
        if objid in metrics:
            metrics[objid]['health'] = h
    return metrics, mesh_bytes


ORIENT_SAMPLE = 70000     # faces scored per candidate; plenty for an area sum
ORIENT_CANDIDATES = 14    # dominant flat faces to try as the new base
# "Sitting flat" has to mean within a few degrees, not perfectly level, or no
# organic model ever qualifies and every suggestion is rejected.
BASE_COS = 0.966          # within 15 degrees of straight down
ORIENT_MIN_BASE = 80.0    # mm2 of near-flat underside before it can sit stably
ORIENT_MAX_TALLER = 1.6   # refuse to make a part much taller than it already is
ORIENT_MIN_GAIN = 300.0   # mm2 saved before it is worth telling anyone


def _face_data(tris):
    """(unit normal, area) per triangle, plus the vertex list. Scoring works on
    normals alone, so the mesh is walked once and never rotated."""
    faces, verts = [], []
    for t in tris:
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
        ux, uy, uz = bx-ax, by-ay, bz-az
        vx, vy, vz = cx-ax, cy-ay, cz-az
        nx = uy*vz - uz*vy; ny = uz*vx - ux*vz; nz = ux*vy - uy*vx
        L = math.sqrt(nx*nx + ny*ny + nz*nz)
        if L == 0:
            continue
        faces.append((nx/L, ny/L, nz/L, L/2))
        verts.extend(t)
    return faces, verts


def _score_up(faces, up, threshold):
    """Unsupported overhang area and plate-contact area if `up` were up.

    A face's slope from horizontal is the angle between its normal and the
    down direction, which is a single dot product. No rotation needed."""
    ux, uy, uz = up
    lim = math.cos(math.radians(threshold))   # normal-to-down cosine at the limit
    over = flat = 0.0
    for nx, ny, nz, a in faces:
        d = -(nx*ux + ny*uy + nz*uz)          # 1.0 = pointing straight down
        if d > lim:
            over += a
        if d > BASE_COS:
            flat += a          # near-down: what can plausibly rest on the plate
    return over, flat


def _extent(verts, up):
    ux, uy, uz = up
    lo = hi = None
    for (x, y, z) in verts:
        d = x*ux + y*uy + z*uz
        if lo is None or d < lo: lo = d
        if hi is None or d > hi: hi = d
    return (hi - lo) if lo is not None else 0.0


def suggest_orientation(tris, bed, threshold=30.0):
    """Which way up leaves the least that has to be held up.

    Only a suggestion: it never rotates anything. The best orientation for
    supports is often the worst for surface finish, for strength across the
    layer lines, or for a painted model whose detail should face upward, and
    none of that is visible from the mesh."""
    faces, verts = _face_data(tris)
    if not faces:
        return None
    faces.sort(key=lambda f: -f[3])
    sample = faces[:ORIENT_SAMPLE]

    cands = [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)]
    seen = [c for c in cands]
    for nx, ny, nz, _ in faces[:400]:          # dominant flats make good bases
        u = (-nx, -ny, -nz)
        if all(u[0]*v[0] + u[1]*v[1] + u[2]*v[2] < 0.985 for v in seen):
            seen.append(u); cands.append(u)
        if len(cands) >= ORIENT_CANDIDATES:
            break

    total = sum(f[3] for f in sample)
    results = []
    for u in cands:
        over, flat = _score_up(sample, u, threshold)
        h = _extent(verts, u)
        results.append({'up': u, 'over': over, 'flat': flat, 'height': h,
                        'fits': h <= bed[2], 'frac': (over/total if total else 0)})
    cur = results[0]

    # Least overhang is not the same as best printed. Standing a flat part on
    # its edge removes every overhang and gives you a fragile tower on a
    # postage stamp, so a candidate has to earn its place: a real footprint to
    # sit on, and no wild increase in height.
    widest = max((r['flat'] for r in results), default=0.0)
    ok = [r for r in results
          if r['fits']
          and r['flat'] >= max(ORIENT_MIN_BASE, widest * 0.15)
          and r['height'] <= cur['height'] * ORIENT_MAX_TALLER]
    if not ok:
        return {'current': cur, 'best': cur, 'gain': 0.0, 'changed': False,
                'reason': 'nothing else gives it a stable enough base to sit on'}
    best = min(ok, key=lambda r: (r['over'], -r['flat'], r['height']))
    gain = cur['over'] - best['over']
    worth = best['up'] != cur['up'] and gain > ORIENT_MIN_GAIN and gain > cur['over'] * 0.25
    return {'current': cur, 'best': best, 'gain': gain, 'changed': worth,
            'reason': None}


def _rotation_to_up(u):
    """Row-vector rotation taking direction `u` to +Z.

    3mf transforms are row-major and apply as p * M + t, which parse_meshes
    already relies on, so the matrix wanted here has e1, e2 and u as its
    COLUMNS. Built from an orthonormal basis rather than an axis-angle formula
    because the determinant is then provably +1 and a mirrored model is the one
    failure that would never announce itself."""
    ux, uy, uz = u
    L = math.sqrt(ux*ux + uy*uy + uz*uz)
    if L == 0:
        return None
    w = (ux/L, uy/L, uz/L)
    a = (1.0, 0.0, 0.0) if abs(w[0]) < 0.9 else (0.0, 1.0, 0.0)
    d = a[0]*w[0] + a[1]*w[1] + a[2]*w[2]
    e1 = (a[0]-d*w[0], a[1]-d*w[1], a[2]-d*w[2])
    n = math.sqrt(sum(c*c for c in e1))
    if n < 1e-9:
        return None
    e1 = tuple(c/n for c in e1)
    e2 = (w[1]*e1[2]-w[2]*e1[1], w[2]*e1[0]-w[0]*e1[2], w[0]*e1[1]-w[1]*e1[0])
    return [[e1[0], e2[0], w[0]],
            [e1[1], e2[1], w[1]],
            [e1[2], e2[2], w[2]]]


def _det3(m):
    return (m[0][0]*(m[1][1]*m[2][2]-m[1][2]*m[2][1])
            - m[0][1]*(m[1][0]*m[2][2]-m[1][2]*m[2][0])
            + m[0][2]*(m[1][0]*m[2][1]-m[1][1]*m[2][0]))


def apply_orientation(tmp, rec, plan):
    """Turn the chosen objects by rewriting their placement, never the mesh.

    Every check that could catch a silently wrong result runs here: the
    rotation must not mirror, the part must land on the plate, and it must
    still fit. A failure returns the file untouched rather than a print that
    starts in the air."""
    rootp = os.path.join(tmp, '3D', '3dmodel.model')
    if not os.path.exists(rootp) or not plan:
        return [], False
    xml = open(rootp, encoding='utf-8').read()
    bed, notes, done = rec['bed'], [], False

    for oid, info in plan.items():
        R = _rotation_to_up(info['up'])
        if R is None or abs(_det3(R) - 1.0) > 1e-6:
            notes.append('object %s not turned: the rotation was not clean' % oid)
            continue
        m = re.search(r'(<item objectid="%s"[^>]*?transform=")([^"]+)(")'
                      % re.escape(oid), xml)
        if not m:
            notes.append('object %s not turned: no placement to rewrite' % oid)
            continue
        v = [float(x) for x in m.group(2).split()]
        if len(v) != 12:
            notes.append('object %s not turned: unexpected placement' % oid)
            continue
        M = [v[0:3], v[3:6], v[6:9]]
        sign_before = _det3(M)
        Mn = [[sum(M[i][k]*R[k][j] for k in range(3)) for j in range(3)]
              for i in range(3)]
        if sign_before * _det3(Mn) <= 0:
            notes.append('object %s not turned: it would have been mirrored' % oid)
            continue
        tn = [sum(v[9+k]*R[k][j] for k in range(3)) for j in range(3)]

        # Where it lands, from the world-space mesh already parsed. Computed
        # eagerly: a generator here is evaluated after the loop variable has
        # moved on, which collapses every point onto the last one and yields a
        # zero-sized part that passes every bed check.
        xs, ys, zs = [], [], []
        for (x, y, z) in info['pts']:
            xs.append(x*R[0][0] + y*R[1][0] + z*R[2][0])
            ys.append(x*R[0][1] + y*R[1][1] + z*R[2][1])
            zs.append(x*R[0][2] + y*R[1][2] + z*R[2][2])
        w, d, h = max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)
        if min(w, d, h) < 0.01:
            notes.append('object %s not turned: it measured as nothing, which '
                         'means the check failed rather than the part' % oid)
            continue
        if w > bed[0] or d > bed[1] or h > bed[2]:
            notes.append('object %s not turned: %.0fx%.0fx%.0fmm would not fit'
                         % (oid, w, d, h))
            continue
        # Keep it exactly where it was in XY. A multi-plate 3mf lays its plates
        # out side by side in ONE coordinate space, so recentring on the bed
        # drags an object off its own plate and on top of whatever is on the
        # first one. Only the drop onto the plate is corrected.
        opx = [q[0] for q in info['pts']]; opy = [q[1] for q in info['pts']]
        tn[0] += (min(opx)+max(opx))/2.0 - (min(xs)+max(xs))/2.0
        tn[1] += (min(opy)+max(opy))/2.0 - (min(ys)+max(ys))/2.0
        tn[2] += -min(zs)

        flat = ' '.join(('%.8g' % x) for x in
                        (Mn[0]+Mn[1]+Mn[2]+tn))
        xml = xml[:m.start(2)] + flat + xml[m.end(2):]
        notes.append('object %s turned: %.0fmm2 -> %.0fmm2 overhang, now '
                     '%.0fx%.0fx%.0fmm and sitting on the plate'
                     % (oid, info['before'], info['after'], w, d, h))
        done = True

    if done:
        open(rootp, 'w', encoding='utf-8', newline='\n').write(xml)
    return notes, done


def orientation_lines(tmp, rec, skip_analyse, plan=None):
    """The orientation advice, worded once and used by both --report and a
    conversion so the two can never say different things. When `plan` is a dict
    it is filled with what would have to change to act on the advice."""
    if skip_analyse:
        return ['orientation not checked (the mesh was not analysed)']
    out, turned = [], False
    for oid, tris in parse_meshes(tmp).items():
        r = suggest_orientation(tris, rec['bed'])
        if not r:
            continue
        c, b = r['current'], r['best']
        if r['changed']:
            turned = True
            if plan is not None:
                pts = [q for t in tris for q in t]
                if len(pts) > 240000:            # enough to bound a solid
                    step = len(pts) // 240000 + 1
                    pts = pts[::step]
                plan[oid] = {'up': b['up'], 'pts': pts,
                             'before': c['over'], 'after': b['over']}
            out.append('object %s would print better turned:' % oid)
            out.append('  as placed  %6.0fmm2 overhang  %5.1fmm tall  '
                       '%5.0fmm2 flat on the plate' % (c['over'], c['height'], c['flat']))
            out.append('  turned     %6.0fmm2 overhang  %5.1fmm tall  '
                       '%5.0fmm2 flat on the plate' % (b['over'], b['height'], b['flat']))
        else:
            out.append('object %s: %s' % (oid, r.get('reason') or
                       'as placed is already the sensible way up'))
    if turned:
        out.append('Prism does not rotate anything. Turning a model changes which '
                   'faces come out smooth and which way the layers run, and the '
                   'mesh cannot tell you that.')
    return out


def plan_supports(metrics, want):
    """Decide whether this model needs supports, and keep them to a minimum.

    The principle is that a support you did not need costs material, time and a
    scarred surface, so nothing is added without a reason that can be stated.

    What it can see: every downward-facing surface, its slope, how high it sits,
    and how far each ceiling has to reach. What it cannot see is the slicer's
    own per-layer view, so a ceiling narrower than the machine's bridge limit is
    trusted to bridge rather than proven to.
    """
    if want == 'off':
        return {'enable_support': '0'}, ['supports off (you asked)']
    if not metrics:
        return {}, ['supports left alone (the mesh was not analysed)']

    ceiling = sum(m['ceiling'] for m in metrics.values())
    steep = sum(m['steep'] for m in metrics.values())
    unbridgeable = sum(m['needs_span'] for m in metrics.values())
    bridgeable = sum(m['bridgeable'] for m in metrics.values())
    span = max((m['max_span'] for m in metrics.values()), default=0.0)
    hi = max((m['over_z_hi'] for m in metrics.values()), default=0.0)
    height = max((m['height'] for m in metrics.values()), default=0.0)

    why = []
    needed = want == 'on' or unbridgeable > SUPPORT_MIN_AREA or steep > 300

    if not needed:
        if ceiling > 0:
            why.append('no supports: %.0fmm2 of ceiling, widest reach %.0fmm, '
                       'all within the %.0fmm the printer bridges'
                       % (ceiling, span, BRIDGE_LIMIT))
        else:
            why.append('no supports: nothing overhangs far enough to need them')
        return {'enable_support': '0'}, why

    out = {'enable_support': '1'}
    if want == 'on' and unbridgeable <= SUPPORT_MIN_AREA:
        why.append('supports on (you asked) though the geometry looks self-supporting')
    else:
        why.append('supports on: %.0fmm2 reaches further than the %.0fmm bridge '
                   'limit, widest span %.0fmm' % (unbridgeable, BRIDGE_LIMIT, span))
    if bridgeable > 1:
        why.append('  %.0fmm2 of shorter ceiling left to bridge on its own'
                   % bridgeable)

    # Only what has to be held up. This is the slicer's own minimum-supports
    # switch and it is the whole point of the exercise.
    out['support_critical_regions_only'] = '1'
    why.append('  limited to critical regions, so nothing is propped up needlessly')

    # Nothing overhangs high up, so supports never need to stand on the model.
    if height > 1 and hi <= height * 0.55:
        out['support_on_build_plate_only'] = '1'
        why.append('  from the build plate only: the highest overhang is %.0fmm '
                   'of %.0fmm' % (hi, height))
    return out, why


def plan_modes(metrics, src_lh, enums):
    """derive the three concrete plans from the model's geometry"""
    dome_objs = [(o, m['up_shallow']) for o, m in (metrics or {}).items() if m['dome']]
    flat_area = max((m['up_flat'] for m in (metrics or {}).values()), default=0.0)
    plans = {}
    for name, spec in MODES.items():
        lh = spec['lh']
        if name != 'speed' and src_lh and src_lh < lh:
            lh = src_lh                      # never coarsen a finer-authored project
        ironing = (name == 'quality' and flat_area > 1000
                   and 'topmost' in enums.get('ironing_type', []))
        plans[name] = {'lh': lh, 'dome_lh': spec['dome'] if spec['dome'] < lh else None,
                       'dome_objs': dome_objs, 'ironing': ironing, 'time': spec['time']}
    return plans


def describe(fname, metrics, plans, support_on):
    lines = [f"{os.path.basename(fname)}:"]
    if metrics is None:
        lines.append("  (mesh analysis skipped, large file)")
    else:
        for o, m in metrics.items():
            bits = []
            if m['oversize']:
                bits.append(f"!! exceeds bed ({m['dims'][0]:.0f}x{m['dims'][1]:.0f}x{m['dims'][2]:.0f}mm)")
            if m['dome']:
                bits.append(f"rounded top {m['up_shallow']:.0f}mm2 -> finer layers advised")
            if m['up_flat'] > 1000:
                bits.append(f"large flat top {m['up_flat']:.0f}mm2 -> ironing helps")
            if m['down_flat'] > 60:
                bits.append(f"overhang {m['down_flat']:.0f}mm2" +
                            ("" if support_on else " (supports are OFF)"))
            if bits:
                lines.append(f"  object {o}: " + "; ".join(bits))
            # Mesh faults last and on their own lines: they are a different
            # kind of problem from a steep overhang. One is about how to print
            # the model, the other about whether the model is printable.
            for hl in health_lines(m.get('health') or {}) if m.get('health') else []:
                lines.append(f"  object {o}: {hl}")
        if len(lines) == 1:
            lines.append("  clean geometry, no special handling needed")
    for name in ('speed', 'balanced', 'quality'):
        p = plans[name]
        d = f", dome objects {p['dome_lh']}" if p['dome_lh'] and p['dome_objs'] else ""
        i = ", ironing" if p['ironing'] else ""
        lines.append(f"  {name:9s} {p['lh']}mm layers{d}{i}  ({p['time']} time)")
    return lines


# --------------------------- conversion --------------------------------

# The handful of settings worth a short flag. Everything else in the profile is
# reachable through --set, which is the advanced door.
QUICK_SETTINGS = {
    'fan':              'fan_max_speed',
    'aux-fan':          'additional_cooling_fan_speed',
    'overhang-fan':     'overhang_fan_speed',
    'infill':           'sparse_infill_pattern',
    'infill-density':   'sparse_infill_density',
    'interface-layers': 'support_interface_top_layers',
    'top-z':            'support_top_z_distance',
    'walls':            'wall_loops',
    'top-layers':       'top_shell_layers',
    'bottom-layers':    'bottom_shell_layers',
    'seam':             'seam_position',
    'brim':             'brim_type',
    'support-style':    'support_style',
}
PERCENT_KEYS = {'sparse_infill_density'}

# Each output is a native project for ONE slicer. Opening a Snapmaker project
# in Creality Print fails on the vendor's own gcode macros with an error that
# looks like a corrupt file, so the target slicer is named everywhere a printer
# is named.
SLICERS = {'cp': 'Creality Print', 'snapmaker': 'Snapmaker Orca',
           'bambu': 'Bambu Studio', 'orca': 'OrcaSlicer'}


def slicer_for(rec):
    return SLICERS.get(rec.get('dialect'), 'OrcaSlicer')


SPECTRUM_BIASES = (25, 50, 75)
SPECTRUM_MAX_LH = 0.20   # colour stack must stay under ~0.2mm to read as blended
SPECTRUM_STEP_MIN = 0.04  # mixed_filament_height_lower_bound
SPECTRUM_STEP_MAX = 0.16  # mixed_filament_height_upper_bound; the printer's own
                          # min/max layer height narrows this further


def spectrum_rows(nslots, biases):
    """Serialized custom mixed-filament rows for every unordered slot pair at
    each blend bias.

    Grammar taken from Snapmaker Orca v2.3.6
    (src/libslic3r/MixedFilament.cpp, MixedFilamentManager::serialize_custom_entries
    and parse_row_definition):

        rows joined by ';'
        row = a,b,enabled,custom,mix_b_percent

    `a` and `b` are 1-based physical slots; the loader rejects a row unless
    both are in 1..nslots and differ.  custom=1 matters: a custom row is built
    fresh, whereas custom=0 marks an auto row that is dropped unless the app
    has already generated that exact pair itself.
    """
    rows, palette = [], []
    for a in range(1, nslots + 1):
        for b in range(a + 1, nslots + 1):
            for mix in biases:
                rows.append(f"{a},{b},1,1,{mix}")
                palette.append((a, b, mix))
    return ';'.join(rows), palette


# ---- painting ----------------------------------------------------------
# Assigning an object to a mixed filament shows the right swatch but does NOT
# blend: Orca only runs the cadence over PAINTED geometry
# (PrintObject.cpp collect_mixed_painted_z_ranges skips any volume whose
# mmu_segmentation_facets are empty), so an assigned-but-unpainted model prints
# as component A. To colour a model without the user touching the paint tools
# we write the segmentation ourselves.

PAINT_ATTR_RE = re.compile(rb'\s+paint_color="[^"]*"')
TRIANGLE_RE = re.compile(rb'(<triangle\b[^>]*?)\s*/>')


def paint_code(state):
    """`paint_color` value for a whole unsplit triangle at `state`.

    Transliterated from TriangleSelector::serialize plus
    FacetsAnnotation::get_triangle_as_string (Snapmaker Orca v2.3.6):
    two bits of split-count (zero here), then either two bits of the state, or
    the marker 0b11 followed by base-15 nibbles of (state - 3). Nibbles are
    read least-significant-bit first and each is PREPENDED to the string."""
    bits = [0, 0]
    if state >= 3:
        bits += [1, 1]
        n = state - 3
        while n >= 15:
            bits += [1, 1, 1, 1]
            n -= 15
        bits += [(n >> i) & 1 for i in range(4)]
    else:
        bits += [state & 1, (state >> 1) & 1]
    out = ''
    for off in range(0, len(bits), 4):
        v = sum(b << i for i, b in enumerate(bits[off:off + 4]))
        out = '0123456789ABCDEF'[v] + out
    return out


def decode_paint_code(code):
    """Inverse of paint_code. None when the triangle is SPLIT (partial painting),
    whose state lives in a subdivision tree we must not rewrite blindly."""
    bits = []
    try:
        for ch in reversed(code):
            d = int(ch, 16)
            bits += [(d >> i) & 1 for i in range(4)]
    except ValueError:
        return None
    if len(bits) < 4 or bits[0] or bits[1]:
        return None
    marker = bits[2] | (bits[3] << 1)
    if marker != 3:
        return marker
    n, off = 0, 4
    while off + 4 <= len(bits):
        nib = sum(bits[off + i] << i for i in range(4))
        off += 4
        n += nib
        if nib != 15:
            return 3 + n
    return None


def collect_painted_states(tmp):
    """Filament ids the source was PAINTED with, and how many triangles each.

    Painting is colour intent just as much as a part assignment is, and a model
    can carry colours that appear nowhere in model_settings."""
    found, split = {}, 0
    base = os.path.join(tmp, '3D')
    for root, _, files in os.walk(base):
        for f in files:
            if not f.lower().endswith('.model'):
                continue
            with open(os.path.join(root, f), 'rb') as fh:
                data = fh.read()
            if b'paint_color=' not in data:
                continue
            for code in set(re.findall(rb'paint_color="([^"]*)"', data)):
                st = decode_paint_code(code.decode('ascii', 'replace'))
                cnt = data.count(b'paint_color="' + code + b'"')
                if st is None:
                    split += cnt
                else:
                    found[st] = found.get(st, 0) + cnt
    return found, split


def remap_paint(data, id_map):
    """Recolour existing segmentation through id_map, preserving the artwork."""
    hits = [0]

    def rep(m):
        st = decode_paint_code(m.group(1).decode('ascii', 'replace'))
        new = id_map.get(st)
        if st is None or new is None or new == st:
            return m.group(0)
        hits[0] += 1
        return b'paint_color="' + paint_code(new).encode('ascii') + b'"'
    return re.sub(rb'paint_color="([^"]*)"', rep, data), hits[0]


def strip_paint(data):
    return PAINT_ATTR_RE.sub(b'', data)


def paint_triangles(data, code):
    """Repaint every triangle in `data`, replacing any existing segmentation."""
    return TRIANGLE_RE.sub(rb'\1 paint_color="' + code.encode('ascii') + rb'"/>',
                           strip_paint(data))


def paint_inner_object(data, inner_id, code):
    m = re.search(rb'<object id="' + str(inner_id).encode('ascii') +
                  rb'"[^>]*>.*?</object>', data, re.S)
    if not m:
        return data, False
    return data[:m.start()] + paint_triangles(m.group(0), code) + data[m.end():], True


def component_map(root_xml):
    """root object id -> [(geometry path, inner object id), ...] in part order."""
    out = {}
    for m in re.finditer(r'<object id="(\d+)"[^>]*>(.*?)</object>', root_xml, re.S):
        comps = []
        for cm in re.finditer(r'<component\b[^>]*>', m.group(2)):
            tag = cm.group(0)
            path = re.search(r'p:path="([^"]+)"', tag)
            oid = re.search(r'objectid="(\d+)"', tag)
            if oid:
                comps.append((path.group(1).lstrip('/') if path else None,
                              oid.group(1)))
        out[m.group(1)] = comps
    return out


def object_part_states(ms_xml):
    """model_settings object id -> (object state, [part states]) after mapping."""
    out = {}
    for m in re.finditer(r'<object id="(\d+)"[^>]*>(.*?)</object>', ms_xml, re.S):
        body = m.group(2)
        head = body.split('<part', 1)[0]
        om = re.search(r'<metadata key="extruder" value="(\d+)"/>', head)
        parts = []
        for pm in re.finditer(r'<part\b[^>]*>(.*?)</part>', body, re.S):
            pe = re.search(r'<metadata key="extruder" value="(\d+)"/>', pm.group(1))
            parts.append(int(pe.group(1)) if pe else
                         (int(om.group(1)) if om else 1))
        out[m.group(1)] = (int(om.group(1)) if om else 1, parts)
    return out


def apply_painting(tmp, ms_xml, id_map, num_physical, report, notes):
    """Write segmentation so assigned blends actually blend.

    Solid filaments need no painting - a plain extruder assignment already
    prints them - so only mixes are painted, which keeps the file from growing
    for no reason."""
    states = object_part_states(ms_xml)
    rootp = os.path.join(tmp, '3D', '3dmodel.model')
    if not os.path.exists(rootp):
        notes.append('no root model, blends cannot be painted')
        return 0
    root_xml = open(rootp, encoding='utf-8', errors='replace').read()
    cmap = component_map(root_xml)

    # targets: geometry file -> {inner object id or None: state}
    targets, unresolved = {}, 0
    for oid, (obj_state, parts) in states.items():
        comps = cmap.get(oid) or []
        if parts and len(comps) >= len(parts):
            for i, st in enumerate(parts):
                path, inner = comps[i]
                if path:
                    targets.setdefault(path, {})[inner] = st
                else:
                    unresolved += 1
        elif comps:
            for path, inner in comps:
                if path:
                    targets.setdefault(path, {})[inner] = obj_state
                else:
                    unresolved += 1
        else:
            unresolved += 1

    painted = recoloured = 0
    for rel, per_inner in targets.items():
        full = os.path.join(tmp, *rel.split('/'))
        if not os.path.exists(full):
            unresolved += 1
            continue
        with open(full, 'rb') as fh:
            data = fh.read()
        before = data

        def handle(block, state):
            # Existing segmentation IS the artwork. Recolour it; never paint
            # over it, which would flatten a multi-colour model to one colour.
            if b'paint_color=' in block:
                return remap_paint(block, id_map) + (0,)
            if state is not None and state > num_physical:
                return (paint_triangles(block, paint_code(state)),
                        0, block.count(b'<triangle '))
            return block, 0, 0

        blocks = list(re.finditer(rb'<object id="(\d+)"[^>]*>.*?</object>',
                                  data, re.S))
        if blocks:
            out, last = [], 0
            for m in blocks:
                inner = m.group(1).decode('ascii')
                blk, rc, pc = handle(m.group(0), per_inner.get(inner))
                out.append(data[last:m.start()])
                out.append(blk)
                last = m.end()
                recoloured += rc
                painted += pc
            out.append(data[last:])
            data = b''.join(out)
        else:
            state = next(iter(set(per_inner.values())), None)
            data, rc, pc = handle(data, state)
            recoloured += rc
            painted += pc

        if data != before:
            with open(full, 'wb') as fh:
                fh.write(data)
    if recoloured:
        report.append('recoloured %s painted triangles onto the blends '
                      '(existing artwork kept)' % format(recoloured, ','))
    if painted:
        report.append('painted %s triangles so the blends actually blend'
                      % format(painted, ','))
    if unresolved:
        notes.append('%d part(s) could not be matched to geometry, those keep '
                     'a plain assignment and will print as the first component'
                     % unresolved)
    return painted


# ---- colour maths: mirrors Snapmaker Orca's own mixed-filament preview ----
# (MixedFilament.cpp: effective_pair_preview_ratios ->
#  build_effective_pair_preview_sequence -> blend_display_color_from_sequence
#  -> blend_color_multi -> filament_mixer_lerp). Reproducing that chain rather
#  than inventing a blend keeps our match agreeing with the swatch he sees.

BASIC_COLOURS = [
    ('Red', (220, 40, 40)), ('Orange', (240, 140, 30)), ('Amber', (240, 190, 40)),
    ('Yellow', (245, 235, 60)), ('Lime', (170, 220, 60)), ('Green', (60, 170, 80)),
    ('Teal', (40, 170, 160)), ('Cyan', (60, 200, 235)), ('Sky', (70, 150, 220)),
    ('Blue', (50, 80, 190)), ('Indigo', (90, 70, 180)), ('Violet', (140, 80, 200)),
    ('Magenta', (215, 60, 150)), ('Pink', (240, 150, 190)), ('Maroon', (130, 45, 60)),
    ('Brown', (135, 95, 60)), ('Olive', (125, 130, 60)), ('Charcoal', (70, 72, 78)),
    ('Grey', (145, 150, 160)), ('Silver', (200, 205, 210)), ('White', (245, 245, 245)),
]


def hex_to_rgb(h):
    h = (h or '').strip().lstrip('#')
    if len(h) == 8:      # #RRGGBBAA - alpha is not part of the pigment
        h = h[:6]
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def rgb_to_hex(rgb):
    return '#%02X%02X%02X' % tuple(max(0, min(255, int(v))) for v in rgb)


def pair_ratios(mix_b):
    """effective_pair_preview_ratios - the integer layer cadence A:B.

    Note this QUANTISES: achievable blends are whole-layer ratios, so many
    nearby percentages collapse onto the same actual colour."""
    mix_b = max(0, min(100, int(mix_b)))
    ra, rb = 1, 0
    if mix_b >= 100:
        ra, rb = 0, 1
    elif mix_b > 0:
        pb, pa = mix_b, 100 - mix_b
        b_major = pb >= pa
        major, minor = (pb, pa) if b_major else (pa, pb)
        layers = max(1, int(math.floor(major / float(max(1, minor)) + 0.5)))
        ra, rb = (1, layers) if b_major else (layers, 1)
    if ra > 0 and rb > 0:
        g = math.gcd(ra, rb)
        ra, rb = ra // g, rb // g
    return max(0, ra), max(0, rb)


def blend_multi(weighted):
    """blend_color_multi - sequential pigment lerp, weights in physical-id order."""
    weighted = [(c, w) for c, w in weighted if w > 0 and c]
    if not weighted:
        return (0, 0, 0)
    cur, acc = weighted[0][0], weighted[0][1]
    for col, w in weighted[1:]:
        total = acc + w
        if total <= 0:
            continue
        cur = mixer.lerp(cur, col, w / float(total))
        acc = total
    return tuple(cur)


def mix_rgb(col_a, col_b, mix_b):
    """Colour Orca will show for one mixed row under the default Simple mode."""
    ra, rb = pair_ratios(mix_b)
    cycle = max(1, ra + rb)
    ca = cb = 0
    for pos in range(cycle):
        if ((pos + 1) * rb) // cycle > (pos * rb) // cycle:
            cb += 1
        else:
            ca += 1
    return blend_multi([(col_a, ca), (col_b, cb)])


def _lab(rgb):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(float(v)) for v in rgb)
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750)
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883

    def f(t):
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t + 16.0 / 116.0)
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def colour_distance(a, b):
    """Perceptual gap (CIE Lab dE76). Plain RGB distance badly misjudges which
    mix looks closest, so it is deliberately not used."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(_lab(a), _lab(b))))


def colour_name(rgb):
    return min(BASIC_COLOURS, key=lambda nc: colour_distance(rgb, nc[1]))[0]


def spectrum_palette(rec, n, biases):
    """Every colour this printer can actually produce: the solids, then each
    generated mix. Index order matches spectrum_rows, because Orca numbers
    virtual filaments from (num_physical + 1) upward over the enabled rows."""
    slots = rec['spectrum']['slots']
    cols = [hex_to_rgb(sl['colour']) or (128, 128, 128) for sl in slots[:n]]
    pal = []
    for i, sl in enumerate(slots[:n]):
        pal.append({'extruder': i + 1, 'rgb': cols[i], 'hex': rgb_to_hex(cols[i]),
                    'solid': True,
                    'label': sl['name'].replace('Semi-Translucent ', '')})
    _, combos = spectrum_rows(n, biases)
    for i, (a, b, mix) in enumerate(combos):
        rgb = mix_rgb(cols[a - 1], cols[b - 1], mix)
        na = slots[a - 1]['name'].replace('Semi-Translucent ', '')
        nb = slots[b - 1]['name'].replace('Semi-Translucent ', '')
        pal.append({'extruder': n + 1 + i, 'rgb': rgb, 'hex': rgb_to_hex(rgb),
                    'solid': False,
                    'label': '%s (%s+%s %d%%)' % (colour_name(rgb), na, nb, mix)})
    return pal


def probe_colour_intent(paths):
    """How many distinct colours the sources actually carry.

    The droplet asks this before offering the colour menu: a model that only
    uses one slot has nothing to map, so defaulting that case to "map
    automatically" just hands back a colour nobody chose."""
    best = 0
    for path in paths:
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if 'Metadata/model_settings.config' not in names:
                    continue
                xml = z.read('Metadata/model_settings.config').decode('utf-8', 'replace')
                used = used_extruders(xml)
                cols = []
                if 'Metadata/project_settings.config' in names:
                    try:
                        cfg = json.loads(z.read('Metadata/project_settings.config'))
                        cols = cfg.get('filament_colour') or []
                    except Exception:
                        cols = []
                if not cols:
                    continue
                distinct = {cols[u - 1] for u in used if 0 < u <= len(cols)}
                best = max(best, len(distinct))
        except Exception:
            continue
    return best


def nearest_colour(target_rgb, palette):
    return min(palette, key=lambda e: colour_distance(target_rgb, e['rgb']))


# A dE76 above this is a visibly different colour, not a near miss. The CMY+grey
# set is semi-translucent with no dark pigment, so dark targets land here.
COLOUR_GAP_WARN = 14.0


def resolve_target(spec, palette):
    """Accept '#RRGGBB', a bare hex, a palette extruder id, or a colour name."""
    t = str(spec).strip()
    if t.isdigit():
        for e in palette:
            if e['extruder'] == int(t):
                return e, 0.0
        raise ValueError('no palette entry with id %s' % t)
    rgb, exact = hex_to_rgb(t), True
    if rgb is None:
        for name, ref in BASIC_COLOURS:
            if name.lower() == t.lower():
                rgb, exact = ref, False   # a name is a rough reference, not a target
                break
    if rgb is None:
        raise ValueError("colour must be #RRGGBB, a palette id, or a name like 'Teal'")
    e = nearest_colour(rgb, palette)
    return e, (colour_distance(rgb, e['rgb']) if exact else 0.0)


def map_source_colours(ms_text, src_cfg, palette, extra_states, report, notes):
    """Repaint the model onto the blended palette.

    Colour intent in a 3mf lives as per-part extruder assignments plus the
    project's filament_colour list, so each used slot is matched to the closest
    colour the printer can actually make and the parts are pointed at it."""
    # A painted model carries colours that never appear in model_settings, so
    # both sources of intent are collected before anything is matched.
    used = sorted(set(used_extruders(ms_text)) | set(extra_states or ()))
    src = [hex_to_rgb(c) for c in ((src_cfg or {}).get('filament_colour') or [])]
    if not src:
        report.append('source declares no filament colours: everything stays on '
                      'slot 1. Pick a colour to print it as a blend')
        return ms_text, 0, {}
    # Every id is remapped, including a model that only uses one. Loading the
    # spectrum filaments redefines what each slot MEANS, so an id left alone
    # silently changes colour: a white model on slot 1 came out cyan.
    mapping, lines, worst = {}, [], 0.0
    for u in used:
        rgb = src[u - 1] if 0 < u <= len(src) and src[u - 1] else None
        stale = rgb is None
        if stale:
            # An id past the source's filament list is stale editing debris.
            # Leaving it alone is the dangerous option: the id still resolves
            # in the new file, silently to an unrelated blend. Fall back to the
            # default filament and say so.
            rgb = src[0] if src else None
            if rgb is None:
                continue
        e = nearest_colour(rgb, palette)
        gap = colour_distance(rgb, e['rgb'])
        worst = max(worst, gap)
        mapping[u] = e['extruder']
        lines.append('  slot %d%s %s -> %s %s%s' % (
            u, ' (not in the source list, used slot 1)' if stale else '',
            rgb_to_hex(rgb), e['hex'], e['label'],
            '  (closest available)' if gap > COLOUR_GAP_WARN else ''))
    if not mapping:
        report.append('model uses several slots but the source carries no colours')
        return ms_text, 0, {}
    ms_text, hits = remap_extruders(ms_text, mapping)
    report.append('colour mapping (%d assignments):' % hits)
    report.extend(lines)
    if worst > COLOUR_GAP_WARN:
        notes.append('some colours are outside what four semi-translucent '
                     'filaments can reach, nearest match used')
    if len(mapping) == 1:
        report.append('  (single-colour model, pick a colour if you want a blend)')
    return ms_text, hits, mapping


def used_extruders(xml_text):
    return sorted({int(v) for v in
                   re.findall(r'<metadata key="extruder" value="(\d+)"/>', xml_text)})


def remap_extruders(xml_text, mapping):
    """Rewrite every extruder assignment through `mapping`; unknown ids stay put."""
    hits = [0]

    def rep(m):
        old = int(m.group(2))
        new = mapping.get(old)
        if new is None or new == old:
            return m.group(0)
        hits[0] += 1
        return m.group(1) + str(new) + m.group(3)
    out = re.sub(r'(<metadata key="extruder" value=")(\d+)("/>)', rep, xml_text)
    return out, hits[0]


def apply_spectrum(out, rec, n, spectrum, plan, notes):
    """Switch on Full Spectrum blending and write the generated mix palette."""
    spec = rec['spectrum']
    rows, palette = spectrum_rows(n, spectrum['biases'])
    out['mixed_filament_definitions'] = rows

    dither = spectrum['dither']
    if dither is None:                      # auto: the documented striping case
        dither = bool(plan.get('dome_objs'))
        if dither:
            notes.append('advanced dithering on (rounded tops detected, '
                          'ordered pattern reduces blend striping)')
    out['mixed_filament_advanced_dithering'] = '1' if dither else '0'

    try:
        lh = float(out.get('layer_height', SPECTRUM_MAX_LH))
    except (TypeError, ValueError):
        lh = SPECTRUM_MAX_LH
    if lh > SPECTRUM_MAX_LH:
        lh = SPECTRUM_MAX_LH
        out['layer_height'] = str(lh)
        notes.append(f'layer height -> {lh} '
                      '(a taller colour stack stops reading as a blend)')

    # Flat faces are a single layer, so at one-layer-A/one-layer-B the colour
    # stack is TWICE the layer height and the top face prints one filament neat.
    # Halving the layer height in painted zones puts a full A+B cycle inside one
    # nominal layer, and the semi-translucent filament then shows the colour
    # underneath. Only painted zones are affected
    # (dithering_step_painted_zones_only), so solid parts keep normal layers.
    # The printer's own floor wins over the blend bounds: asking for a layer
    # thinner than the machine allows is not a finer blend, it is a bad file.
    def _f(key, default):
        v = out.get(key, default)
        v = v[0] if isinstance(v, list) and v else v
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    lo = max(SPECTRUM_STEP_MIN, _f('min_layer_height', SPECTRUM_STEP_MIN))
    hi = min(SPECTRUM_STEP_MAX, _f('max_layer_height', SPECTRUM_STEP_MAX))

    step = spectrum.get('step')
    asked = step
    if step is None:
        step = lh / 2.0
    if step:
        step = round(min(max(step, lo), hi), 3)
        if asked and abs(asked - step) > 1e-9:
            notes.append('blend step %smm is outside what this printer allows '
                         '(%s-%smm), using %smm' % (asked, lo, hi, step))
        # Set the LAYER HEIGHT itself rather than dithering_z_step_size. That
        # option thins only painted zones, which leaves supports and the prime
        # tower on the original height; they then run past the top of the model
        # and the slice fails on max print height. One height for everything
        # keeps every object on the same layers.
        out['layer_height'] = str(step)
        out['dithering_z_step_size'] = '0'
        out['dithering_local_z_mode'] = '0'
        out['mixed_filament_gradient_mode'] = '0'
        notes.append('blend layers %smm: a full colour cycle fits in %smm, so '
                     'flat faces stop banding. About %.1fx the layers, and the '
                     'print takes about that much longer.'
                     % (step, round(step * 2, 3), lh / step))
        if plan.get('dome_lh'):
            # every layer is already finer than any dome refinement would be
            plan['dome_lh'] = None
    else:
        notes.append('blend layers off, flat faces will show one filament')

    initials = [s['name'].replace('Semi-Translucent ', '')[0].upper()
                for s in spec['slots']]
    pairs = sorted({f"{initials[a-1]}+{initials[b-1]}" for a, b, _ in palette})
    notes.append(f"full spectrum: {n} slots set to {spec['filament_key']} "
                 f"({', '.join(i for i in initials)})")
    notes.append(f"  {len(palette)} mixes: {', '.join(pairs)} "
                 f"at {'/'.join(str(b) for b in spectrum['biases'])}%")
    return palette


def build_project_settings(src, rec, single, plan, notes, spectrum=None,
                           keep_source=False, overrides=None, supports=None):
    tpl = rec['template']
    out = dict(tpl)
    fil_table = rec['filaments']
    tpl_n = len(tpl['filament_settings_id'])
    if spectrum:
        spec = rec['spectrum']
        n = len(spec['slots'])
        types = [spec['filament_key']] * n
        colours = [sl['colour'] for sl in spec['slots']]
    elif single:
        n = tpl_n
        stype = norm_type(((src or {}).get('filament_type') or ['PLA'])[0])
        types = [stype] * n
        col = single if single.startswith('#') else rec['default_colour']
        colours = [col] * n
    else:
        src_ids = (src or {}).get('filament_settings_id') or []
        n = max(tpl_n, len(src_ids))
        stypes = (src or {}).get('filament_type') or ['PLA']
        types = [norm_type(stypes[i] if i < len(stypes) else stypes[0]) for i in range(n)]
        scol = (src or {}).get('filament_colour') or []
        tcol = tpl.get('filament_colour', ['#808080'])
        colours = [(scol[i] if i < len(scol) else tcol[i % len(tcol)]) for i in range(n)]

    profiles = []
    for i, t in enumerate(types):
        prof = fil_table.get(t)
        if prof is None:
            notes.append(f"slot {i+1}: no {t} profile for {rec['label']}, using PLA profile")
            prof = fil_table['PLA']
        profiles.append(prof)

    fil_keys = set().union(*[set(p['values']) for p in profiles]) - META_SKIP
    for k in fil_keys:
        if k not in tpl: continue
        tv = tpl[k]
        if not isinstance(tv, list): continue
        row = []
        for p in profiles:
            v = p['values'].get(k, tv[:1] or [''])
            row.append(v[0] if isinstance(v, list) and v else (v if not isinstance(v, list) else tv[0]))
        out[k] = row
    out['filament_settings_id'] = [p['id'] for p in profiles]
    out['filament_colour'] = colours
    for k, tv in tpl.items():
        if k.startswith('filament_') and isinstance(tv, list) and len(tv) == tpl_n \
           and k not in fil_keys and k not in ('filament_settings_id', 'filament_colour') and tv:
            out[k] = [tv[0]] * n
    if 'flush_volumes_matrix' in tpl:
        off = next((x for x in tpl['flush_volumes_matrix'] if x != '0'), '280')
        out['flush_volumes_matrix'] = [('0' if i == j else off) for i in range(n) for j in range(n)]
    if 'flush_volumes_vector' in tpl:
        out['flush_volumes_vector'] = [tpl['flush_volumes_vector'][0] if tpl['flush_volumes_vector'] else '140'] * (2 * n)

    enums = rec['enums']
    carried, dropped, carried_keys = [], [], set()
    for k in CARRY_KEYS:
        if not src or k not in src or k not in tpl: continue
        v = src[k]
        if isinstance(v, list): v = v[0] if v else None
        if v is None or v == out.get(k): continue
        if k in ENUM_KEYS and len(str(v)) > 4 and enums.get(k) and str(v) not in enums[k]:
            dropped.append(f"{k}={v} (not supported on {rec['label']})")
            continue
        out[k] = v
        carried_keys.add(k)
        carried.append(f"{k}={v}")
    if carried: notes.append("carried: " + ", ".join(carried))
    if dropped: notes.append("dropped: " + ", ".join(dropped))

    # Standing preferences for this machine. They run after the carry, so they
    # are the last word: these are the operator's settings for their own
    # printer, not the designer's guess about someone else's. Anything they
    # override is named, so nothing changes silently. --keep-source turns them
    # off and leaves the source and vendor profile to decide.
    wanted = {} if keep_source else dict(rec.get('defaults') or {})
    wanted.update(supports or {})       # measured from this model's own geometry
    chosen = set(overrides or ())
    wanted.update(overrides or {})      # an explicit choice always wins
    applied = []
    for k, v in sorted(wanted.items()):
        if k not in out:
            notes.append(f"{k} is not a setting {rec['label']} has, ignored")
            continue
        if k in ENUM_KEYS and enums.get(k) and str(v) not in enums[k]:
            notes.append(f"default {k}={v} not supported on {rec['label']}, "
                         f"left at {out[k]}")
            continue
        was = out.get(k)
        if isinstance(was, list):
            # a per-slot setting: every loaded filament gets the same value
            newv = [str(v)] * len(was)
            if was == newv:
                continue
            out[k] = newv
        else:
            if str(was) == str(v):
                continue
            out[k] = str(v)
        # the cooling logic ramps between min and max, so a cap below the
        # current minimum would leave the two crossed over
        if k == 'fan_max_speed' and isinstance(out.get('fan_min_speed'), list):
            try:
                cap = float(v)
                out['fan_min_speed'] = [str(int(min(float(x), cap)))
                                        for x in out['fan_min_speed']]
            except (TypeError, ValueError):
                pass
        applied.append(f"{k}={v}" + ("  [yours]" if k in chosen else "")
                       + (f" (source asked for {was})"
                          if k in carried_keys else f" (was {was})"))
    if applied:
        notes.append("settings applied: " + ", ".join(applied))

    # mode application
    out['layer_height'] = str(plan['lh'])
    if plan['ironing'] and str(out.get('ironing_type', 'no ironing')) in ('no ironing', ''):
        out['ironing_type'] = 'topmost'
        notes.append("ironing enabled (large flat top)")

    if spectrum:
        apply_spectrum(out, rec, n, spectrum, plan, notes)

    # A 3mf names a system preset AND carries the resolved values. The slicer
    # decides which to believe from `different_settings_to_system`: its first
    # entry lists the process keys that differ from the named preset, and
    # anything absent is silently reloaded from that preset. Ours said nothing
    # differed, so the slicer reverted every setting Prism had changed.
    # The list is derived from the actual diff against the baked template, so
    # it cannot fall out of step with what was written.
    # mixed_filament_* and dithering_* are project-level, not part of any
    # process preset, and already survive a preset reload untouched. Listing
    # them here would claim they belong to a preset that has never heard of
    # them, so they are left out rather than risked.
    NOT_PROCESS = ('mixed_filament_', 'dithering_', 'mixed_color_')
    fil_keys = set()
    for prof in (rec.get('filaments') or {}).values():
        fil_keys |= set(prof.get('values') or ())
    changed = sorted(k for k, v in out.items()
                     if not k.startswith('filament_')
                     and not k.startswith(NOT_PROCESS)
                     and k not in fil_keys
                     and k in tpl and tpl[k] != v
                     and not isinstance(v, list))
    # Per-slot settings such as the fans live in the FILAMENT presets. Declared
    # in the process entry they would be ignored and reloaded, which is exactly
    # how the infill silently reverted.
    changed_fil = sorted(k for k, v in out.items()
                         if k in fil_keys and k in tpl and tpl[k] != v)
    # Shape is [process, one per filament slot, printer]. The Creality-dialect
    # templates omit the key entirely even though Creality projects use it, and
    # a captured template can carry the wrong number of slots, so it is rebuilt
    # to the right length rather than patched in place.
    old_dss = out.get('different_settings_to_system')
    old_dss = list(old_dss) if isinstance(old_dss, list) else []
    proc_entry = old_dss[0] if old_dss else ''
    printer_entry = old_dss[-1] if len(old_dss) >= 2 else ''
    fil_entries = old_dss[1:-1] if len(old_dss) > 2 else []
    fil_entries = (list(fil_entries) + [''] * n)[:n]
    if changed_fil:
        fil_entries = [';'.join([x for x in e.split(';') if x and x not in changed_fil]
                                + changed_fil) for e in fil_entries]
    keep = [x for x in proc_entry.split(';') if x and x not in changed]
    out['different_settings_to_system'] = (
        [';'.join(keep + changed)] + fil_entries + [printer_entry])
    if changed or changed_fil:
        notes.append(f"{len(changed) + len(changed_fil)} setting(s) marked as "
                     "modified so the slicer keeps them")

    out['version'] = rec['project_version']
    out['from'] = 'project'
    return out, n


def clean_model_settings(xml_text, single):
    xml_text = re.sub(r'\s*<metadata key="filament_map_mode" value="[^"]*"/>', '', xml_text)
    xml_text = re.sub(r'\s*<metadata key="filament_maps" value="[^"]*"/>', '', xml_text)
    if single:
        xml_text = re.sub(r'(<metadata key="extruder" value=")\d+("/>)', r'\g<1>1\g<2>', xml_text)
    return xml_text


def inject_object_layer_height(xml_text, objid, value):
    m = re.search(r'(<object id="%s">.*?)(</object>)' % re.escape(objid),
                  xml_text, flags=re.S)
    if not m: return xml_text, False
    if 'key="layer_height"' in m.group(1): return xml_text, False
    block = m.group(1)
    nm = re.search(r'[ \t]*<metadata key="name"[^>]*/>\n', block)
    ins = '    <metadata key="layer_height" value="%s"/>\n' % value
    if nm:
        block = block[:nm.end()] + ins + block[nm.end():]
    else:
        block = block.replace('>', '>\n' + ins, 1)
    return xml_text[:m.start(1)] + block + xml_text[m.end(1):], True


def convert(src_path, rec, key, mode, single, dome_override, skip_analyse,
            out_path=None, spectrum=None, keep_source=False, overrides=None,
            supports=None, orient=False):
    stem = re.sub(r'\.3mf$', '', os.path.basename(src_path), flags=re.I)
    suffix = key.upper() + ('-FS' if spectrum else '')
    out_path = out_path or os.path.join(os.path.dirname(src_path),
                                        f"{stem} - {suffix}.3mf")
    notes, report = [], []
    tmp = tempfile.mkdtemp(prefix='3mfopt_')
    try:
        with zipfile.ZipFile(src_path) as z:
            z.extractall(tmp)
        sp = os.path.join(tmp, 'Metadata', 'project_settings.config')
        src_cfg = None
        if os.path.exists(sp):
            try:
                src_cfg = json.load(open(sp, encoding='utf-8'))
            except Exception:
                notes.append('source project settings unreadable, template defaults used')
        else:
            notes.append('no source project settings, template defaults used')

        metrics, mesh_bytes = analyse_file(tmp, rec['bed'], skip_analyse)
        src_lh = None
        try:
            v = (src_cfg or {}).get('layer_height')
            src_lh = float(v[0] if isinstance(v, list) else v)
        except (TypeError, ValueError):
            pass
        plans = plan_modes(metrics, src_lh, rec['enums'])
        plan = dict(plans[mode])
        if dome_override == 'off':
            plan['dome_lh'] = None
        elif dome_override:
            plan['dome_lh'] = dome_override

        if orient and mesh_bytes <= ANALYSE_BUDGET_BYTES:
            # NB: not `plan` — that is the mode plan, and shadowing it here
            # broke every conversion. Second time this exact mistake has cost
            # an hour in this file.
            turn_plan = {} if orient == 'apply' else None
            report.extend(orientation_lines(tmp, rec, skip_analyse, turn_plan))
            if turn_plan:
                lines, done = apply_orientation(tmp, rec, turn_plan)
                report.extend(lines)
                if done:
                    report.append('  the mesh is untouched; only where it sits '
                                  'on the plate has changed')

        sup_cfg, sup_why = ({}, [])
        if supports:
            sup_cfg, sup_why = plan_supports(metrics, supports)
            report.extend(sup_why)
        new_cfg, nslots = build_project_settings(src_cfg, rec, single, plan,
                                                 notes, spectrum, keep_source,
                                                 overrides, sup_cfg)
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        json.dump(new_cfg, open(sp, 'w', encoding='utf-8', newline='\n'), indent=4)

        ms = os.path.join(tmp, 'Metadata', 'model_settings.config')
        support_on = str(new_cfg.get('enable_support', '0')) == '1'
        if os.path.exists(ms):
            ms_text = open(ms, encoding='utf-8').read()   # read FIRST, a
            # write-mode open in the same expression truncates before the read
            ms_text = clean_model_settings(ms_text, bool(single))
            id_map = {}
            if spectrum:
                pal = spectrum_palette(rec, nslots, spectrum['biases'])
                painted_states, split_tris = collect_painted_states(tmp)
                if split_tris:
                    notes.append('%s partly-painted triangles left as they are '
                                 '(their state lives in a subdivision tree)'
                                 % format(split_tris, ','))
                if spectrum.get('colour') is not None:
                    ent, gap = resolve_target(spectrum['colour'], pal)
                    every = set(used_extruders(ms_text)) | set(painted_states)
                    id_map = {u: ent['extruder'] for u in every}
                    ms_text, hits = remap_extruders(ms_text, id_map)
                    report.append('whole model -> %s %s (%d assignments)'
                                  % (ent['hex'], ent['label'], hits))
                    if len(painted_states) > 1:
                        notes.append('the source was painted in %d colours; one '
                                     'chosen colour replaces all of them'
                                     % len(painted_states))
                    if gap > COLOUR_GAP_WARN:
                        notes.append('requested colour is outside the achievable '
                                     'gamut, closest blend used')
                elif spectrum.get('map', True):
                    ms_text, _, id_map = map_source_colours(
                        ms_text, src_cfg, pal, painted_states, report, notes)
            if plan['dome_lh'] and plan['dome_objs']:
                for objid, area in plan['dome_objs']:
                    ms_text, ok = inject_object_layer_height(ms_text, objid, str(plan['dome_lh']))
                    if ok:
                        report.append(f"object {objid}: rounded top ({area:.0f}mm2) -> layer height {plan['dome_lh']}")
            open(ms, 'w', encoding='utf-8', newline='\n').write(ms_text)
            if spectrum:
                apply_painting(tmp, ms_text, id_map, nslots, report, notes)
        elif spectrum and (spectrum.get('colour') is not None or spectrum.get('map', True)):
            report.append('no per-object settings block, colours cannot be '
                          'assigned in this file')
        elif plan['dome_lh'] and plan['dome_objs']:
            report.append(f"rounded top detected, already covered by global layer height {plan['lh']}"
                          if plan['lh'] <= plan['dome_lh'] else
                          f"rounded top detected but no per-object settings block, global {plan['lh']} applies")

        open(os.path.join(tmp, 'Metadata', 'slice_info.config'), 'w',
             encoding='utf-8', newline='\n').write(rec['slice_info'])
        if rec.get('app_stamp'):
            mp = os.path.join(tmp, '3D', '3dmodel.model')
            if os.path.exists(mp):
                t = open(mp, encoding='utf-8').read()
                t2 = re.sub(r'(<metadata name="Application">)[^<]*',
                            r'\g<1>' + rec['app_stamp'], t, count=1)
                if t2 != t:
                    open(mp, 'w', encoding='utf-8', newline='\n').write(t2)

        if metrics:
            for o, m in metrics.items():
                if m['oversize']:
                    report.append(f"!! object {o} ({m['dims'][0]:.0f}x{m['dims'][1]:.0f}x{m['dims'][2]:.0f}mm) exceeds {rec['label']} bed {rec['bed'][0]}x{rec['bed'][1]}x{rec['bed'][2]}")
                if m['down_flat'] > 60 and not support_on:
                    report.append(f"object {o}: {m['down_flat']:.0f}mm2 overhang, supports are OFF; consider enabling")
        elif mesh_bytes > ANALYSE_BUDGET_BYTES:
            report.append(f"mesh analysis skipped ({mesh_bytes//1_000_000} MB of mesh data)")

        if os.path.exists(out_path): os.remove(out_path)
        files = []
        for r, _, fs in os.walk(tmp):
            for f in fs:
                p = os.path.join(r, f)
                files.append((os.path.relpath(p, tmp), p))
        files.sort(key=lambda t: (t[0] != '[Content_Types].xml', t[0]))
        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
            for rel, p in files:
                z.write(p, rel)

        with zipfile.ZipFile(src_path) as z0, zipfile.ZipFile(out_path) as z1:
            # paint_color is segmentation, not geometry. Compare with it
            # stripped from BOTH sides: vertices and triangle indices must still
            # match byte for byte, so the mesh guarantee is unchanged.
            geo0 = {n: hashlib.sha256(strip_paint(z0.read(n))).hexdigest()
                    for n in z0.namelist() if n.startswith('3D/')
                    and not (rec.get('app_stamp') and n == '3D/3dmodel.model')}
            geo1 = {n: hashlib.sha256(strip_paint(z1.read(n))).hexdigest()
                    for n in geo0}
            assert geo0 == geo1, 'geometry changed, aborting'
            json.loads(z1.read('Metadata/project_settings.config'))
            if 'Metadata/model_settings.config' in z0.namelist():
                s0 = z0.read('Metadata/model_settings.config').decode('utf-8')
                s1 = z1.read('Metadata/model_settings.config').decode('utf-8')
                ET.fromstring(s1)
                for tag in ('<object ', '<plate', '<model_instance'):
                    assert s0.count(tag) == s1.count(tag), \
                        f'model_settings lost {tag} entries, aborting'

        print(f"OK -> {out_path}")
        print(f"open in: {slicer_for(rec)}")
        print(f"printer: {rec['label']}  |  mode: {mode} ({plan['lh']}mm)  |  process: {new_cfg['print_settings_id']}")
        fmap = ", ".join(f"{i+1}:{p}" for i, p in enumerate(new_cfg['filament_settings_id'][:4]))
        print(f"filaments ({nslots}): {fmap}")
        for n_ in notes: print("note: " + n_)
        for g in report: print("geometry: " + g)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_report(files, rec, skip_analyse, orient=False):
    for f in files:
        tmp = tempfile.mkdtemp(prefix='3mfrep_')
        try:
            with zipfile.ZipFile(f) as z:
                z.extractall(tmp)
            sp = os.path.join(tmp, 'Metadata', 'project_settings.config')
            src_cfg = {}
            if os.path.exists(sp):
                try: src_cfg = json.load(open(sp, encoding='utf-8'))
                except Exception: pass
            src_lh = None
            try:
                v = src_cfg.get('layer_height')
                src_lh = float(v[0] if isinstance(v, list) else v)
            except (TypeError, ValueError):
                pass
            metrics, _ = analyse_file(tmp, rec['bed'], skip_analyse)
            plans = plan_modes(metrics, src_lh, rec['enums'])
            support_on = str(src_cfg.get('enable_support', '0')) == '1'
            for line in describe(f, metrics, plans, support_on):
                print(line)
            if orient:
                for line in orientation_lines(tmp, rec, skip_analyse):
                    print('  ' + line)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--printer')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--interactive', action='store_true')
    ap.add_argument('--mode', choices=list(MODES), default='balanced')
    ap.add_argument('--single', nargs='?', const='auto', default=None)
    ap.add_argument('--spectrum', action='store_true',
                    help='Full Spectrum colour blending (printers that support it)')
    ap.add_argument('--spectrum-biases', default=','.join(str(b) for b in SPECTRUM_BIASES),
                    help='blend percentages per pair (default 25,50,75)')
    ap.add_argument('--spectrum-dither', choices=['auto', 'on', 'off'], default='auto',
                    help="ordered dithering; 'auto' enables it when rounded tops are found")
    ap.add_argument('--spectrum-colour', '--spectrum-color', dest='spectrum_colour',
                    default=None, metavar='C',
                    help="print the whole model as one blend: #RRGGBB, a palette id, or a name")
    ap.add_argument('--spectrum-map', choices=['auto', 'off'], default='auto',
                    help="map the source's own colours onto blends (default auto)")
    ap.add_argument('--spectrum-step', default=None, metavar='MM',
                    help="layer height in painted zones; 'off' keeps normal layers "
                         "(default: half the layer height, which hides flat-face banding)")
    ap.add_argument('--spectrum-list', action='store_true',
                    help='print every colour this printer can make, then exit')
    ap.add_argument('--spectrum-probe', action='store_true',
                    help='print how many distinct colours the file carries, then exit')
    ap.add_argument('--dome', default=None,
                    help="override dome-object layer height ('off' disables)")
    ap.add_argument('--no-analyse', action='store_true')
    for flag, key in sorted(QUICK_SETTINGS.items()):
        ap.add_argument('--' + flag, dest='qs_' + flag.replace('-', '_'),
                        default=None, metavar='V', help='set ' + key)
    ap.add_argument('--set', action='append', dest='sets', default=None,
                    metavar='KEY=VALUE',
                    help='set any profile key directly; repeatable')
    ap.add_argument('--list-settings', action='store_true',
                    help='print the settings you can change, then exit')
    # Deliberately two switches rather than one flag with an optional value:
    # `--orient file.3mf` would otherwise swallow the filename as the value.
    ap.add_argument('--orient', action='store_true',
                    help='say which way up needs the least support')
    ap.add_argument('--orient-apply', action='store_true', dest='orient_apply',
                    help='and turn it for you')
    ap.add_argument('--supports', choices=['auto', 'on', 'off'], default=None,
                    help='look at the model and add supports only where they are '
                         'actually needed')
    ap.add_argument('--keep-source', action='store_true',
                    help="skip this printer's standing defaults and keep the "
                         "source and vendor values")
    ap.add_argument('--out')
    ap.add_argument('files', nargs='*')
    a = ap.parse_args()

    def parse_biases(text):
        try:
            vals = [int(x) for x in text.split(',') if x.strip()]
        except ValueError:
            ap.error('--spectrum-biases must be whole numbers, comma separated')
        if not vals or any(not 0 <= b <= 100 for b in vals):
            ap.error('--spectrum-biases must each be between 0 and 100')
        return sorted(set(vals))

    # --single also takes an optional value, so rescue a model file that landed
    # on it rather than silently dropping both the file and the colour.
    if a.single and str(a.single).lower().endswith('.3mf') and os.path.exists(a.single):
        a.files.insert(0, a.single)
        a.single = 'auto'

    idx = load_index()
    if a.list_settings:
        if not a.printer:
            ap.error('--list-settings needs --printer')
        rec = load_printer(a.printer)
        tpl, enums = rec['template'], rec['enums']
        print("short flags:")
        for flag, key in sorted(QUICK_SETTINGS.items()):
            cur = tpl.get(key, '-')
            cur = cur[0] if isinstance(cur, list) and cur else cur
            dflt = (rec.get('defaults') or {}).get(key)
            print("  --%-17s %-30s now %s%s"
                  % (flag, key, cur,
                     '  (Prism sets %s)' % dflt if dflt else ''))
        print("\nvalues allowed for the enum settings:")
        for k in sorted(enums):
            print("  %-30s %s" % (k, ', '.join(sorted(enums[k]))))
        print("\nanything else in the profile: --set KEY=VALUE  (%d keys)"
              % len(tpl))
        return
    if a.spectrum_probe:
        print(probe_colour_intent([os.path.abspath(f) for f in a.files]))
        return
    if a.spectrum_list:
        if not a.printer:
            ap.error('--spectrum-list needs --printer')
        rec = load_printer(a.printer)
        if not rec.get('spectrum'):
            ap.error("%s has no colour blending" % rec['label'])
        pal = spectrum_palette(rec, len(rec['spectrum']['slots']),
                               parse_biases(a.spectrum_biases))
        print("%-4s %-9s %s" % ('id', 'colour', 'made from'))
        for e in pal:
            print("%-4d %-9s %s%s" % (e['extruder'], e['hex'], e['label'],
                                      '  (loaded filament)' if e['solid'] else ''))
        return
    if a.list:
        for k in idx['order']:
            p = idx['printers'][k]
            tag = f"   [{p['spectrum']}]" if p.get('spectrum') else ''
            sl = SLICERS.get(p.get('dialect'), '')
            print(f"{k:14s} {p['label']}{tag}" + (f"   -> {sl}" if sl else ''))
        return
    if not a.files:
        ap.error('no input files')

    if a.interactive:
        keys = idx['order']
        for i, k in enumerate(keys, 1):
            print(f"  {i:2d}) {idx['printers'][k]['label']}")
        sel = input("Printer number: ").strip()
        key = keys[int(sel) - 1]
        rec = load_printer(key)
        run_report(a.files, rec, a.no_analyse, a.orient or a.orient_apply)
        m = input("Mode [1=speed 2=balanced 3=quality] (2): ").strip() or '2'
        mode = {'1': 'speed', '2': 'balanced', '3': 'quality'}[m]
        if rec.get('spectrum') and not a.spectrum:
            names = ', '.join(sl['name'].replace('Semi-Translucent ', '')
                              for sl in rec['spectrum']['slots'])
            print(f"  Full Spectrum blends {len(rec['spectrum']['slots'])} filaments "
                  f"({names}) into a wider palette.")
            if (input("Use Full Spectrum? [y/N]: ").strip().lower() or 'n').startswith('y'):
                a.spectrum = True
                print("  Colour: blank maps the model's own colours onto blends;")
                print("  or give #RRGGBB / a name / an id from --spectrum-list.")
                c = input("Colour (blank = map automatically): ").strip()
                if c:
                    a.spectrum_colour = c
    else:
        if not a.printer:
            ap.error('--printer is required (or use --interactive / --list)')
        key = a.printer
        rec = load_printer(key)
        if a.report:
            run_report(a.files, rec, a.no_analyse, a.orient or a.orient_apply)
            return
        mode = a.mode

    single = None
    if a.single:
        single = a.single if a.single.startswith('#') else rec['default_colour']

    overrides = {}
    # NB: not `key` — that holds the printer key, and rebinding it here renamed
    # every output file after the last entry in QUICK_SETTINGS.
    for flag, setting in QUICK_SETTINGS.items():
        v = getattr(a, 'qs_' + flag.replace('-', '_'), None)
        if v is not None:
            overrides[setting] = v
    for item in (a.sets or []):
        if '=' not in item:
            ap.error('--set wants KEY=VALUE, got %r' % item)
        k, v = item.split('=', 1)
        overrides[k.strip()] = v.strip()
    for k, v in list(overrides.items()):
        if k in PERCENT_KEYS and not str(v).endswith('%'):
            overrides[k] = str(v) + '%'
        allowed = rec['enums'].get(k)
        if allowed and str(overrides[k]) not in allowed:
            ap.error('%s=%s is not supported on %s. Allowed: %s'
                     % (k, v, rec['label'], ', '.join(sorted(allowed))))

    spectrum = None
    if a.spectrum:
        if not rec.get('spectrum'):
            ap.error(f"--spectrum is not available for {rec['label']}")
        if single:
            ap.error('--single collapses to one loaded filament; for a blended '
                     'colour use --spectrum-colour instead')
        biases = parse_biases(a.spectrum_biases)
        step = None
        if a.spectrum_step is not None:
            if str(a.spectrum_step).strip().lower() in ('off', '0', 'none'):
                step = 0.0
            else:
                try:
                    step = float(a.spectrum_step)
                except ValueError:
                    ap.error("--spectrum-step must be a layer height in mm, or 'off'")
                if not SPECTRUM_STEP_MIN <= step <= SPECTRUM_STEP_MAX:
                    ap.error('--spectrum-step must be between %s and %s mm'
                             % (SPECTRUM_STEP_MIN, SPECTRUM_STEP_MAX))
        spectrum = {'biases': biases,
                    'dither': None if a.spectrum_dither == 'auto'
                              else a.spectrum_dither == 'on',
                    'colour': a.spectrum_colour,
                    'map': a.spectrum_map != 'off',
                    'step': step}
        if a.spectrum_colour is not None:
            try:
                resolve_target(a.spectrum_colour,
                               spectrum_palette(rec, len(rec['spectrum']['slots']),
                                                biases))
            except ValueError as exc:
                ap.error(str(exc))

    if a.out and len(a.files) > 1:
        ap.error('--out only valid with a single input file')
    for f in a.files:
        convert(os.path.abspath(f), rec, key, mode, single, a.dome,
                a.no_analyse, a.out, spectrum, a.keep_source, overrides,
                a.supports,
                'apply' if a.orient_apply else ('suggest' if a.orient else None))


if __name__ == '__main__':
    main()
