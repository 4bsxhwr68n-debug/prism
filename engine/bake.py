#!/usr/bin/env python3
"""Prism bake v2: registry-driven, multi-printer, four dialects.

Builds data/printers/<key>.json for every registry entry by resolving the
installed slicers' vendor profile trees, matching processes/filaments by
resolved compatible_printers (the mechanism slicers themselves use).

Dialects (template shape + version stamps a target slicer accepts):
  cp        Creality Print 7.1  (proven: creatful_pink.3mf)
  snapmaker Snapmaker Orca 2.3.5 (proven: senna U1 file)
  bambu     Bambu Studio 2.x    (proven: MakerWorld rack file, BambuStudio-02.01.01.52)
  orca      generic OrcaSlicer  (Orca 2.3.5 shape + Bambu-style stamps)
"""
import json, os, sys, zipfile, glob, subprocess

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out_v2')
CP_ROOT = "/Applications/Creality Print.app/Contents/Resources/profiles"
SM_ROOT = "/Applications/Snapmaker Orca.app/Contents/Resources/profiles"
CP_BIN = "/Applications/Creality Print.app/Contents/MacOS/CrealityPrint"
SM_BIN = "/Applications/Snapmaker Orca.app/Contents/MacOS/Snapmaker_Orca"

META = {'name','inherits','from','instantiation','setting_id','filament_id','print_settings_id',
        'printer_settings_id','filament_settings_id','compatible_printers','compatible_printers_condition',
        'compatible_prints','compatible_prints_condition','version','is_custom_defined','type','id',
        'printer_model','printer_variant','printer_technology','default_print_profile','default_filament_profile',
        'upward_compatible_machine','support_multi_bed_types','bed_type'}

# ---------------- registry: key -> (vendor_root, vendor, machine leaf, dialect, label)
REG = {
 # Creality (CP dialect — output opens natively in Creality Print)
 'k2':        (CP_ROOT,'Creality','Creality K2 0.4 nozzle','cp','Creality K2'),
 'k2plus':    (CP_ROOT,'Creality','Creality K2 Plus 0.4 nozzle','cp','Creality K2 Plus'),
 'k2pro':     (CP_ROOT,'Creality','Creality K2 Pro 0.4 nozzle','cp','Creality K2 Pro'),
 'k1c':       (CP_ROOT,'Creality','Creality K1C 0.4 nozzle','cp','Creality K1C'),
 'k1max':     (CP_ROOT,'Creality','Creality K1 Max 0.4 nozzle','cp','Creality K1 Max'),
 'k1se':      (CP_ROOT,'Creality','Creality K1 SE 0.4 nozzle','cp','Creality K1 SE'),
 'ender3v3':  (CP_ROOT,'Creality','Creality Ender-3 V3 0.4 nozzle','cp','Creality Ender-3 V3'),
 'ender3v3ke':(CP_ROOT,'Creality','Creality Ender-3 V3 KE 0.4 nozzle','cp','Creality Ender-3 V3 KE'),
 'hi':        (CP_ROOT,'Creality','Creality Hi 0.4 nozzle','cp','Creality Hi'),
 # Snapmaker (Snapmaker Orca dialect)
 'u1':        (SM_ROOT,'Snapmaker','Snapmaker U1 (0.4 nozzle)','snapmaker','Snapmaker U1'),
 # Bambu Lab (Bambu Studio dialect; profiles from SM tree = newer Orca sync)
 'x1c':       (SM_ROOT,'BBL','Bambu Lab X1 Carbon 0.4 nozzle','bambu','Bambu Lab X1 Carbon'),
 'p1s':       (SM_ROOT,'BBL','Bambu Lab P1S 0.4 nozzle','bambu','Bambu Lab P1S'),
 'p1p':       (SM_ROOT,'BBL','Bambu Lab P1P 0.4 nozzle','bambu','Bambu Lab P1P'),
 'a1':        (SM_ROOT,'BBL','Bambu Lab A1 0.4 nozzle','bambu','Bambu Lab A1'),
 'a1mini':    (SM_ROOT,'BBL','Bambu Lab A1 mini 0.4 nozzle','bambu','Bambu Lab A1 mini'),
 'x1e':       (SM_ROOT,'BBL','Bambu Lab X1E 0.4 nozzle','bambu','Bambu Lab X1E'),
 # Other brands (generic Orca dialect, SM tree)
 'mk4s':      (SM_ROOT,'Prusa','Prusa MK4S 0.4 nozzle','orca','Prusa MK4S'),
 'coreone':   (SM_ROOT,'Prusa','Prusa CORE One 0.4 nozzle','orca','Prusa CORE One'),
 'neptune4pro':(SM_ROOT,'Elegoo','Elegoo Neptune 4 Pro (0.4 nozzle)','orca','Elegoo Neptune 4 Pro'),
 'voron24-300':(SM_ROOT,'Voron','Voron 2.4 300 0.4 nozzle','orca','Voron 2.4 300'),
 'qidiq1pro': (SM_ROOT,'Qidi','Qidi Q1 Pro 0.4 nozzle','orca','Qidi Q1 Pro'),
 'sv06':      (SM_ROOT,'Sovol','Sovol SV06 0.4 nozzle','orca','Sovol SV06'),
 'kobra2':    (SM_ROOT,'Anycubic','Anycubic Kobra 2 0.4 nozzle','orca','Anycubic Kobra 2'),
 'ad5x':      (SM_ROOT,'Flashforge','Flashforge AD5X 0.4 nozzle','orca','Flashforge AD5X'),
}

BRAND_PREF = {  # per-vendor preferred filament brand keywords (rank order)
 'Creality': ['Hyper','CR-','Generic'], 'Snapmaker': ['SnapSpeed','Snapmaker','Generic'],
 'BBL': ['Bambu','Generic'], 'Prusa': ['Prusament','Generic'],
}
TYPES = ['PLA','PLA-CF','PLA-SILK','PETG','PETG-CF','ABS','ASA','TPU','PA','PET','PVA','PP']

# Printers with a vendor colour-blending mode. The blend filaments are
# deliberately NOT part of pick_filaments: they are semi-translucent, so the
# 'decorative' rule there excludes them from being anyone's default PLA.
# Standing preferences, applied after the source's own settings. Every printer
# in the registry runs a 0.4mm nozzle at a 0.2mm layer height, so these carry
# across unchanged; there is no machine here that wants a different number.
# Each value is still enum-checked per printer before it is written.
DEFAULTS_ALL = {
 'sparse_infill_pattern':        'gyroid',   # isotropic, no crossings
 'support_interface_top_layers': '3',        # cleaner surface under supports
 'support_top_z_distance':       '0.25',     # 1.25x layer height, easier release
}
DEFAULTS_BY_PRINTER = {}   # per-machine exceptions; none needed so far

DIALECT_OF = {}   # filled as each printer bakes; the index needs it

SPECTRUM = {
 'u1': {'label':'Full Spectrum', 'vendor':'Snapmaker',
        'preset':'Snapmaker PLA Full Spectrum @U1 0.4 nozzle',
        'family':'PLA Full Spectrum',
        'fallback':[('Semi-Translucent Cyan','#08ABFB'),('Semi-Translucent Magenta','#D93B90'),
                    ('Semi-Translucent Yellow','#F9ED3D'),('Semi-Translucent Gray','#9199A4')]},
}


def spectrum_slots(root, spec):
    """Slot palette from the vendor colour library, so a vendor SKU refresh
    flows through on the next bake instead of being frozen here."""
    lib=os.path.join(root,spec['vendor'],'filament','filaments_colours.json')
    try:
        d=json.load(open(lib,encoding='utf-8'))
        for f in d.get('filaments',[]):
            if f.get('filament_type')!=spec['family'] or not f.get('enabled',True): continue
            out=[]
            for c in f.get('filament_color',[]):
                if not c.get('enabled',True): continue
                col=c.get('filament_color') or []
                if not col: continue
                out.append({'sku':c.get('sku',''),
                            'name':(c.get('color_name') or {}).get('en','') or 'Colour',
                            'colour':col[0]})
            if len(out)>=2: return out
    except Exception as e:
        print(f"[warn] spectrum colour library unreadable ({type(e).__name__}), using fallback")
    return [{'sku':'','name':n,'colour':c} for n,c in spec['fallback']]

_res_cache = {}
def resolve(root, vendor, name, depth=0):
    key=(root,vendor,name)
    if key in _res_cache: return _res_cache[key]
    assert depth<14, name
    p=None
    for v2 in (vendor,'OrcaFilamentLibrary','Snapmaker','Creality'):
        for sub in ('machine','process','filament',''):
            c=os.path.join(root,v2,sub,name+'.json')
            if os.path.exists(c): p=c; break
        if p: break
    if p is None: raise FileNotFoundError(f"{vendor}/{name}")
    d=json.load(open(p, encoding='utf-8'))
    base={}
    if d.get('inherits'):
        base=dict(resolve(root,vendor,d['inherits'],depth+1))
    base.update(d)
    _res_cache[key]=base
    return base

def norm_type(t):
    t=(t or 'PLA').upper()
    if 'SILK' in t: return 'PLA-SILK'
    for k in ('PLA-CF','PETG-CF','PETG','PLA','ABS','ASA','TPU','PVA','PET','PA','PP'):
        if t.startswith(k): return k
    return t

def leaves(root, vendor, sub):
    for f in sorted(glob.glob(os.path.join(root,vendor,sub,'*.json'))):
        name=os.path.splitext(os.path.basename(f))[0]
        try: d=json.load(open(f, encoding='utf-8'))
        except Exception: continue
        if d.get('instantiation','true') in ('true',True):
            yield name

def compatible(root, vendor, name, machine):
    try: r=resolve(root,vendor,name)
    except Exception: return None
    cps=r.get('compatible_printers') or []
    if cps and machine not in cps: return None
    return r

def pick_process(root, vendor, machine):
    """quality-biased: 0.2 High Quality/Quality > 0.2 Standard/Optimal > any 0.2 > any"""
    cands=[]
    for name in leaves(root,vendor,'process'):
        r=compatible(root,vendor,name,machine)
        if r is None: continue
        lh=str(r.get('layer_height',''))
        nl=name.lower()
        score=0
        if lh in ('0.2','0.20'): score+=100
        elif lh in ('0.16','0.18'): score+=60
        if 'high quality' in nl or 'quality' in nl: score+=30
        elif 'standard' in nl or 'optimal' in nl: score+=20
        if any(w in nl for w in ('draft','speed','strength','support','hueforge','special')): score-=50
        toks=[t for t in machine.replace('(',' ').replace(')',' ').split()
              if t.lower() not in ('nozzle','0.4','0.2','0.6','0.8') and len(t)>1]
        if toks and any(t.lower() in nl for t in toks[-2:]): score+=15
        cands.append((score,name,r))
    if not cands: return None,None
    cands.sort(key=lambda x:(-x[0],x[1]))
    return cands[0][1],cands[0][2]

def pick_filaments(root, vendor, machine):
    by_type={}
    pref=BRAND_PREF.get(vendor,['Generic'])
    def scan_into(names):
        for name in names:
            r=compatible(root,vendor,name,machine)
            yield name,r
    scan=list(scan_into(leaves(root,vendor,'filament')))
    if not any(r and norm_type((r.get('filament_type') or ['PLA'])[0] if isinstance(r.get('filament_type'),list) else r.get('filament_type'))=='PLA' for _,r in scan):
        # vendor ships no filaments compatible with this machine (Voron has no
        # filament dir; Sovol only SV08 ones): use the cross-vendor
        # OrcaFilamentLibrary (compat-universal, bases resolved via donors)
        scan+=list(scan_into(leaves(root,'OrcaFilamentLibrary','filament')))
    for name,r in scan:
        if r is None: continue
        ft=r.get('filament_type'); ft=ft[0] if isinstance(ft,list) else ft
        t=norm_type(ft)
        if t not in TYPES: continue
        nl=name
        rank=len(pref)
        for i,kw in enumerate(pref):
            if kw.lower() in nl.lower(): rank=i; break
        # prefer plain profiles over CF/Silk/special names for the base type
        low=nl.lower()
        # decorative/specialty types are never a sane default, whatever the brand
        decorative=any(w in low for w in ('silk','glow','wood','galaxy','sparkle','marble','luminous',
                                          'dynamic','metal','glitter','fluo','aero','l-w','lw-','translucent',
                                          'benchy','support'))
        perf=sum(w in low for w in ('matte','tough','eco','hf','high speed','cf','gf','light'))
        if 'basic' in low or 'snapspeed' in low: perf-=1
        if t in ('PLA-SILK','PLA-CF','PETG-CF'): decorative=False; perf=0
        score=(decorative,rank,perf,len(nl))
        if t not in by_type or score<by_type[t][0]:
            by_type[t]=(score,name,r)
    return {t:{'id':n,'values':{k:v for k,v in r.items() if k not in META}}
            for t,(s,n,r) in by_type.items()}

# ---------------- dialect templates + stamps
def cp_template():
    return json.loads(zipfile.ZipFile(os.path.expanduser('~/Downloads/creatful_pink.3mf'))
                      .read('Metadata/project_settings.config'))
def orca_template():
    return json.loads(zipfile.ZipFile(os.path.expanduser('~/Downloads/Senna_helmet_lamp-U1-optimised.3mf'))
                      .read('Metadata/project_settings.config'))
def bambu_template():
    t=json.loads(zipfile.ZipFile(os.path.expanduser('~/Downloads/Filament+Rack+-+Mega+Pack.3mf'))
                 .read('Metadata/project_settings.config'))
    # widen to 4 filament slots (AMS-sized); rack project has 2
    n=len(t['filament_settings_id'])
    if n<4:
        for k,v in list(t.items()):
            if isinstance(v,list) and len(v)==n and (k.startswith('filament')
               or k in ('nozzle_temperature','nozzle_temperature_initial_layer')):
                t[k]=v+[v[0]]*(4-n)
        t['filament_settings_id']=[t['filament_settings_id'][0]]*4
        t['filament_colour']=(t['filament_colour']+['#808080']*4)[:4]
        t['flush_volumes_matrix']=[('0' if i==j else '280') for i in range(4) for j in range(4)]
        t['flush_volumes_vector']=['140']*8
    return t

def harvest_enums(bins):
    CANDS={'sparse_infill_pattern':['concentric','zig-zag','grid','line','cubic','triangles','tri-hexagon','gyroid','honeycomb','adaptivecubic','alignedrectilinear','3dhoneycomb','hilbertcurve','archimedeanchords','octagramspiral','supportcubic','lightning','crosshatch','zigzag','crosszag','lockedzag','quartercubic'],
           'brim_type':['no_brim','outer_only','inner_only','outer_and_inner','auto_brim','brim_ears','painted'],
           'support_type':['normal(auto)','tree(auto)','normal(manual)','tree(manual)','normal','tree','hybrid(auto)'],
           'support_style':['default','grid','snug','organic','tree_slim','tree_strong','tree_hybrid','tree_organic'],
           'seam_position':['nearest','aligned','back','random','rear'],
           'wall_generator':['classic','arachne'],
           'ironing_type':['no ironing','top','topmost','solid']}
    sets=[]
    for b in bins:
        ss=set(subprocess.run(['strings','-a',b],capture_output=True,text=True).stdout.splitlines())
        sets.append({k:{v for v in vs if v in ss or len(v)<=4} for k,vs in CANDS.items()})
    out={}
    for k in CANDS:
        common=set.intersection(*[s[k] for s in sets])
        out[k]=sorted(common)
    return out

DIALECTS={
 'cp':       {'template':cp_template,   'bins':[CP_BIN],       'project_version':'7.1.0.4414',
              'app_stamp':None,
              'slice_info':'<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n    <header_item key="X-CX-Client-Type" value="creality_print"/>\n    <header_item key="X-CX-Client-Version" value="07.01.00.4414"/>\n  </header>\n</config>\n'},
 'snapmaker':{'template':orca_template, 'bins':[SM_BIN],       'project_version':'2.3.5',
              'app_stamp':'BambuStudio-2.3.5',
              'slice_info':'<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n    <header_item key="X-BBL-Client-Type" value="slicer"/>\n    <header_item key="X-BBL-Client-Version" value=""/>\n  </header>\n</config>\n'},
 'orca':     {'template':orca_template, 'bins':[SM_BIN],       'project_version':'2.3.5',
              'app_stamp':'BambuStudio-2.2.0',
              'slice_info':'<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n    <header_item key="X-BBL-Client-Type" value="slicer"/>\n    <header_item key="X-BBL-Client-Version" value=""/>\n  </header>\n</config>\n'},
 'bambu':    {'template':bambu_template,'bins':[CP_BIN,SM_BIN],'project_version':'02.01.01.52',
              'app_stamp':'BambuStudio-02.01.01.52',
              'slice_info':'<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n    <header_item key="X-BBL-Client-Type" value="slicer"/>\n    <header_item key="X-BBL-Client-Version" value="02.01.01.52"/>\n  </header>\n</config>\n'},
}

def overlay(tpl, prof, per_filament=False, slots=4):
    """overlay a resolved profile onto the flat template (proven rules:
    machine/process lists verbatim; filament values replicated per slot)"""
    changed=0
    for k,v in prof.items():
        if k in META or k not in tpl: continue
        tv=tpl[k]
        if isinstance(v,str) and ',' in v and isinstance(tv,list):
            v=v.split(',')          # CP machine profiles: "0x0,260x0,..." string
        if isinstance(v,list) and not v: continue
        if isinstance(tv,list):
            if per_filament:
                base=v[0] if isinstance(v,list) else v
                nv=[base]*len(tv)
            else:
                nv=v if isinstance(v,list) else [v]*len(tv)
        else:
            nv=v[0] if isinstance(v,list) else v
        if tpl[k]!=nv: changed+=1
        tpl[k]=nv
    return changed

def bed_from_area(area):
    pts=[tuple(float(x) for x in p.split('x')) for p in area]
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    return [round(max(xs)-min(xs)), round(max(ys)-min(ys))]

def main():
    os.makedirs(os.path.join(OUT,'printers'), exist_ok=True)
    enum_cache={}
    ok=[]
    for key,(root,vendor,machine,dialect,label) in REG.items():
        try:
            m=resolve(root,vendor,machine)
            pname,pres=pick_process(root,vendor,machine)
            if not pname: print(f"[skip] {key}: no compatible process"); continue
            fils=pick_filaments(root,vendor,machine)
            if 'PLA' not in fils: print(f"[skip] {key}: no PLA filament"); continue
            d=DIALECTS[dialect]
            tpl=d['template']()
            overlay(tpl,m)
            overlay(tpl,pres)
            overlay(tpl,fils['PLA']['values'],per_filament=True)
            tpl['printer_settings_id']=machine
            tpl['print_settings_id']=pname
            n=len(tpl['filament_settings_id'])
            tpl['filament_settings_id']=[fils['PLA']['id']]*n
            tpl['version']=d['project_version']
            bed=bed_from_area(tpl['printable_area'] if isinstance(tpl['printable_area'],list) else m['printable_area'])
            bed.append(round(float(tpl.get('printable_height') or m.get('printable_height') or 250)))
            bkey=tuple(sorted(d['bins']))
            if bkey not in enum_cache: enum_cache[bkey]=harvest_enums(list(bkey))
            rec={'label':label,'dialect':dialect,'printer_id':machine,'process_id':pname,
                 'bed':bed,'project_version':d['project_version'],'app_stamp':d['app_stamp'],
                 'slice_info':d['slice_info'],'enums':enum_cache[bkey],
                 'default_colour':'#FFFFFF' if key=='u1' else '#000000',
                 'template':tpl,'filaments':fils}
            want = dict(DEFAULTS_ALL); want.update(DEFAULTS_BY_PRINTER.get(key, {}))
            enums_for = rec['enums']
            rec['defaults'] = {k: v for k, v in want.items()
                               if not enums_for.get(k) or v in enums_for[k]}
            if key in SPECTRUM:
                spec=SPECTRUM[key]
                try:
                    fr=resolve(root,vendor,spec['preset'])
                    fils['PLA-FS']={'id':spec['preset'],
                                    'values':{k:v for k,v in fr.items() if k not in META}}
                    rec['spectrum']={'filament_key':'PLA-FS','label':spec['label'],
                                     'slots':spectrum_slots(root,spec)}
                except Exception as e:
                    print(f"[warn] {key}: no spectrum preset ({type(e).__name__}: {e})")
            DIALECT_OF[key]=dialect
            json.dump(rec,open(os.path.join(OUT,'printers',key+'.json'),'w'),indent=1)
            ok.append((key,label,pname,sorted(fils),bed))
        except Exception as e:
            print(f"[skip] {key}: {type(e).__name__}: {e}")
    json.dump({'printers':{k:dict({'label':l,'dialect':DIALECT_OF.get(k,'')},
                                  **({'spectrum':SPECTRUM[k]['label']} if k in SPECTRUM else {}))
                           for k,l,_,_,_ in ok},
               'order':[k for k,_,_,_,_ in ok]},
              open(os.path.join(OUT,'index.json'),'w'),indent=1)
    print(f"\nBAKED {len(ok)}/{len(REG)}:")
    for k,l,p,f,bed in ok:
        print(f"  {k:12s} {l:24s} bed={bed} process={p[:46]:46s} mats={len(f)}")

if __name__=='__main__':
    main()
