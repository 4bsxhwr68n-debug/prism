"""Import OBJ and STL into a 3mf project.

Prism's job is to hand a slicer a project it understands. A 3mf carries
geometry AND intent: units, placement, per-object settings, filament
assignments. OBJ and STL carry geometry and almost nothing else, so importing
one is not a format conversion, it is supplying the intent that was never in
the file.

Two things are genuinely absent and both fail silently if guessed:

  UNITS. A 3mf is millimetres. An OBJ or STL is bare numbers. The same file is
  a 43mm trinket or a 1.1 metre monument depending on whether its author worked
  in millimetres or inches, and nothing in the file says which.

  UP AXIS. Printing is Z up. Most OBJ exporters, Blender included, write Y up.
  Read it wrong and the model lies on its side, which looks plausible and
  quietly ruins every overhang, orientation and support decision downstream.

So this module measures, states what it cannot know, and lets the caller
decide. It never picks for you when the answer is in doubt.
"""
import os
import re
import struct
import zipfile

MM_PER = {'mm': 1.0, 'cm': 10.0, 'm': 1000.0, 'inch': 25.4}
# Below this, in millimetres, a model is too small to be a print rather than a
# miniature: almost always a file authored in metres or inches.
TOO_SMALL_MM = 5.0
# Above this it is larger than any consumer plate by a wide margin, so the
# units are more likely wrong than the model genuinely enormous.
TOO_LARGE_MM = 2000.0
# Decimal places at which two vertices are the same vertex. 1e-5 mm is far
# below any printer's resolution and well above float noise from a transform.
WELD_PLACES = 5


def _tris_from_faces(verts, faces):
    """Triangles as index triples. OBJ faces may be quads or n-gons, so fan
    triangulate from the first corner, which is correct for the convex faces
    exporters produce and harmless for the rest."""
    out = []
    for f in faces:
        for i in range(1, len(f) - 1):
            out.append((f[0], f[i], f[i + 1]))
    return out


def parse_obj(path):
    """Vertices, triangles, and the material each triangle was tagged with.

    Only v, f, usemtl and mtllib are read. Normals and texture coordinates are
    deliberately ignored: a slicer recomputes normals from winding, and
    textures have no meaning on a filament printer."""
    verts, faces, face_mtl = [], [], []
    cur, mtllib = None, None
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if not line or line[0] == '#':
                continue
            parts = line.split()
            if not parts:
                continue
            tag = parts[0]
            if tag == 'v' and len(parts) >= 4:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif tag == 'f' and len(parts) >= 4:
                idx = []
                for p in parts[1:]:
                    # "v", "v/vt", "v//vn" and "v/vt/vn" all start with the
                    # vertex index, which is all that matters here.
                    n = int(p.split('/')[0])
                    # OBJ indices are 1 based, and negative means relative to
                    # the end of the list so far.
                    idx.append(n - 1 if n > 0 else len(verts) + n)
                faces.append(idx)
                face_mtl.append(cur)
            elif tag == 'usemtl' and len(parts) >= 2:
                cur = parts[1]
            elif tag == 'mtllib' and len(parts) >= 2:
                mtllib = ' '.join(parts[1:])
    tris, tri_mtl = [], []
    for f, m in zip(faces, face_mtl):
        for i in range(1, len(f) - 1):
            tris.append((f[0], f[i], f[i + 1]))
            tri_mtl.append(m)
    return verts, tris, tri_mtl, mtllib


def parse_mtl(path):
    """Material name to #RRGGBB, from the diffuse colour.

    Kd is 0..1 floats. Everything else in an MTL describes how light behaves on
    a surface, which a filament printer cannot act on."""
    out, cur = {}, None
    if not os.path.exists(path):
        return out
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == 'newmtl' and len(parts) >= 2:
                cur = parts[1]
            elif parts[0] == 'Kd' and cur and len(parts) >= 4:
                r, g, b = (max(0.0, min(1.0, float(x))) for x in parts[1:4])
                out[cur] = '#%02X%02X%02X' % (round(r * 255), round(g * 255),
                                              round(b * 255))
    return out


def parse_stl(path):
    """Vertices and triangles from a binary or ASCII STL.

    STL stores three loose vertices per triangle with no indices, so the same
    point recurs and is welded here. The format check is the file SIZE, not the
    leading word: plenty of binary STLs begin with "solid" because the exporter
    wrote a header that looks like ASCII."""
    size = os.path.getsize(path)
    with open(path, 'rb') as fh:
        head = fh.read(84)
        binary = False
        if len(head) == 84:
            (count,) = struct.unpack('<I', head[80:84])
            binary = size == 84 + count * 50
        if binary:
            fh.seek(84)
            raw = fh.read(count * 50)
            verts, tris, seen = [], [], {}
            for i in range(count):
                off = i * 50 + 12          # skip this facet's normal
                tri = []
                for c in range(3):
                    p = struct.unpack_from('<3f', raw, off + c * 12)
                    k = (round(p[0], 5), round(p[1], 5), round(p[2], 5))
                    j = seen.get(k)
                    if j is None:
                        j = seen[k] = len(verts)
                        verts.append((p[0], p[1], p[2]))
                    tri.append(j)
                tris.append(tuple(tri))
            return verts, tris
    # ASCII
    verts, tris, seen, cur = [], [], {}, []
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 4 and parts[0] == 'vertex':
                p = (float(parts[1]), float(parts[2]), float(parts[3]))
                k = (round(p[0], 5), round(p[1], 5), round(p[2], 5))
                j = seen.get(k)
                if j is None:
                    j = seen[k] = len(verts)
                    verts.append(p)
                cur.append(j)
                if len(cur) == 3:
                    tris.append(tuple(cur)); cur = []
            elif parts and parts[0] == 'endfacet':
                cur = []
    return verts, tris


def weld(verts, tris, places=WELD_PLACES):
    """Merge vertices that sit at the same point, renumbering the triangles.

    Not a nicety. A mesh whose triangles share no vertices has every edge
    belonging to exactly one triangle, which is the definition of non-manifold,
    and a slicer says so: "40524 non-manifold edges" on a 13508 triangle model
    is three edges per triangle and none of them joined. STL cannot express
    sharing at all, and plenty of OBJ exporters do not bother, so this is the
    normal state of an imported mesh rather than a damaged one."""
    canon, out, remap = {}, [], []
    for v in verts:
        k = (round(v[0], places), round(v[1], places), round(v[2], places))
        j = canon.get(k)
        if j is None:
            j = canon[k] = len(out)
            out.append(v)
        remap.append(j)
    kept = []
    for t in tris:
        a, b, c = remap[t[0]], remap[t[1]], remap[t[2]]
        # A triangle whose corners collapsed onto each other has no area and
        # no meaning, and slicers report those separately as their own fault.
        if a != b and b != c and a != c:
            kept.append((a, b, c))
    return out, kept


def bad_edges(tris):
    """Edges not shared by exactly two triangles, which is what a slicer counts
    and calls non-manifold."""
    e = {}
    for a, b, c in tris:
        if a == b or b == c or a == c:
            continue
        for x, y in ((a, b), (b, c), (c, a)):
            k = (x, y) if x < y else (y, x)
            e[k] = e.get(k, 0) + 1
    return sum(1 for n in e.values() if n != 2)


def weld_tagged(verts, tris, tags):
    """weld(), keeping each triangle's material tag attached to it."""
    canon, out, remap = {}, [], []
    for v in verts:
        k = (round(v[0], WELD_PLACES), round(v[1], WELD_PLACES),
             round(v[2], WELD_PLACES))
        j = canon.get(k)
        if j is None:
            j = canon[k] = len(out)
            out.append(v)
        remap.append(j)
    kept, kept_tags = [], []
    for t, m in zip(tris, tags):
        a, b, c = remap[t[0]], remap[t[1]], remap[t[2]]
        if a != b and b != c and a != c:
            kept.append((a, b, c)); kept_tags.append(m)
    return out, kept, kept_tags


def bbox(verts):
    if not verts:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def dims(verts):
    lo, hi = bbox(verts)
    return tuple(hi[i] - lo[i] for i in range(3))


def unit_candidates(verts):
    """Plausible readings of this file's units, best first.

    Ordered by how print-like the result is rather than by convention, because
    the question being answered is "which of these is a thing someone meant to
    print"."""
    d = dims(verts)
    biggest = max(d) if d else 0.0
    out = []
    for name, f in MM_PER.items():
        mm = biggest * f
        plausible = TOO_SMALL_MM <= mm <= TOO_LARGE_MM
        out.append({'unit': name, 'factor': f,
                    'dims': tuple(x * f for x in d), 'plausible': plausible})
    out.sort(key=lambda c: (not c['plausible'], abs(c['dims'][0] * 0 + max(c['dims']) - 60)))
    return out


def looks_y_up(verts):
    """Whether this reads like a Y up export.

    A weak signal deliberately: it reports a suspicion, never a conclusion. The
    caller asks. Most printable models are no taller than they are wide, so a
    file whose Y extent dominates its Z extent is usually Y up."""
    d = dims(verts)
    return d[1] > d[2] * 1.5 and d[1] > d[0]


def to_z_up(verts):
    """Y up to Z up: Y becomes Z, Z becomes negative Y. A rotation, so the mesh
    keeps its handedness and nothing is mirrored."""
    return [(x, -z, y) for (x, y, z) in verts]


def scale_and_seat(verts, factor):
    """Scale to millimetres and sit the model on the plate at the origin, the
    placement a slicer expects to receive."""
    v = [(x * factor, y * factor, z * factor) for (x, y, z) in verts]
    lo, _ = bbox(v)
    return [(x - lo[0], y - lo[1], z - lo[2]) for (x, y, z) in v]


CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                 '</Types>')

RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel-1" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        '</Relationships>')


def build_model_xml(objects):
    """The 3dmodel.model for one or more meshes, each its own object and item.

    Split by material where an OBJ had them, so colours survive as separate
    objects the slicer can assign filaments to, which is how Prism's colour
    mapping already expects to find them."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<model unit="millimeter" xml:lang="en-US" '
           'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
           '<resources>']
    for oid, (verts, tris, _colour) in enumerate(objects, start=1):
        out.append('<object id="%d" type="model"><mesh><vertices>' % oid)
        out += ['<vertex x="%.6g" y="%.6g" z="%.6g"/>' % v for v in verts]
        out.append('</vertices><triangles>')
        out += ['<triangle v1="%d" v2="%d" v3="%d"/>' % t for t in tris]
        out.append('</triangles></mesh></object>')
    out.append('</resources><build>')
    for oid in range(1, len(objects) + 1):
        out.append('<item objectid="%d" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>' % oid)
    out.append('</build></model>')
    return '\n'.join(out)


def write_3mf(path, objects):
    """A minimal, valid 3mf. Three files is the whole requirement; everything
    else a slicer writes is its own preference, and Prism's normal pipeline
    supplies that afterwards from the chosen printer's profile."""
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', CONTENT_TYPES)
        z.writestr('_rels/.rels', RELS)
        z.writestr('3D/3dmodel.model', build_model_xml(objects))
    return path


def split_by_material(verts, tris, tri_mtl, colours):
    """One object per material, so colour intent survives as structure.

    Vertices are renumbered per object rather than shared, because a slicer
    treats each object as a separate body and a shared vertex list would make
    them one."""
    groups = {}
    for t, m in zip(tris, tri_mtl):
        groups.setdefault(m, []).append(t)
    if len(groups) <= 1:
        return [(verts, tris, None)]
    out = []
    for m, ts in groups.items():
        remap, vs, nts = {}, [], []
        for t in ts:
            nt = []
            for i in t:
                j = remap.get(i)
                if j is None:
                    j = remap[i] = len(vs)
                    vs.append(verts[i])
                nt.append(j)
            nts.append(tuple(nt))
        out.append((vs, nts, (colours or {}).get(m)))
    return out


def read_mesh_file(path):
    """(objects, colours, warnings) for any supported mesh file.

    objects is a list of (verts, tris, colour_or_None) in the file's own units
    and axes. Nothing is scaled or turned here: that needs a decision this
    function is not entitled to make."""
    ext = os.path.splitext(path)[1].lower()
    warn = []
    if ext == '.obj':
        verts, tris, tri_mtl, mtllib = parse_obj(path)
        # Weld ONLY if it helps. Merging vertices that an author deliberately
        # kept apart, where two surfaces touch without being joined, creates a
        # non-manifold junction that was not there. Measured on real files,
        # welding an already sound mesh took one from 13 bad edges to 21. So
        # try it and keep the better result, rather than assuming.
        before, before_t = len(verts), len(tris)
        cand_v, cand_t, cand_m = weld_tagged(verts, tris, tri_mtl)
        if bad_edges(cand_t) <= bad_edges(tris):
            verts, tris, tri_mtl = cand_v, cand_t, cand_m
        else:
            warn.append('left as the author stored it: merging its vertices '
                        'would have joined surfaces that are meant to be '
                        'separate')
            before, before_t = len(verts), len(tris)
        if before_t != len(tris):
            warn.append('%d triangle(s) had no area once merged and were left out'
                        % (before_t - len(tris)))
        if before != len(verts):
            warn.append('%d vertices merged to %d: the file stored them '
                        'separately, which every slicer reads as non-manifold'
                        % (before, len(verts)))
        colours = {}
        if mtllib:
            mpath = os.path.join(os.path.dirname(path), mtllib)
            colours = parse_mtl(mpath)
            if not colours:
                warn.append('the .obj names %s but it could not be read, so '
                            'colours come from the printer instead' % mtllib)
        objects = split_by_material(verts, tris, tri_mtl, colours)
        return objects, colours, warn
    if ext == '.stl':
        verts, tris = parse_stl(path)
        return [(verts, tris, None)], {}, warn
    raise ValueError('not a mesh file Prism can read: %s' % ext)
