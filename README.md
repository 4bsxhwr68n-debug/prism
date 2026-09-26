<img src="docs/logo.png" alt="Prism" width="110" align="right">

# Prism

**Retarget any 3MF. Blend any colour.**

![licence AGPL-3.0](https://img.shields.io/badge/licence-AGPL--3.0-2f7d62)
![macOS Windows Linux](https://img.shields.io/badge/macOS%20%7C%20Windows%20%7C%20Linux-supported-2f7d62)
![no dependencies](https://img.shields.io/badge/dependencies-none-2f7d62)
![24 printers](https://img.shields.io/badge/printers-24-2f7d62)

Prism takes a 3MF project built for one printer and retargets it to another, so
it opens as a proper native project rather than a pile of broken settings. It
covers 24 machines from Creality, Snapmaker, Bambu Lab, Prusa, Elegoo, Voron,
Qidi, Sovol, Anycubic and Flashforge.

It also works out where supports are genuinely needed, tells you which way up
needs the least of them, and on the Snapmaker U1 does Full Spectrum colour
without you mixing or painting anything.

![The Prism window](docs/header.png)

## Install

Download the [latest release](https://github.com/4bsxhwr68n-debug/prism/releases/latest).
Nothing to install on any platform.

| | |
|---|---|
| **macOS** | Unzip, drag `Prism.app` where you like. Signed and notarised, so it just opens. |
| **Windows** | Unzip, double-click `Prism.exe`. SmartScreen warns once: More info, Run anyway. |
| **Linux** | Unpack, run `./prism`. |

The Mac app is signed by Tradelynx Ltd and notarised by Apple, and it carries
its own Python, so it needs nothing installed and opens like any other app. On an
Intel Mac it falls back to a path that still wants the Xcode Command Line Tools:
the bundled engine is arm64 for now.

Windows is not signed, so SmartScreen warns once. Getting past that costs a
click; getting past the macOS equivalent used to cost a trip through System
Settings, which is why the Mac side was worth paying for first.

Smaller `-script` variants are also attached for anyone who already has Python 3.
On Linux those need `zenity`, `kdialog` or `yad` for the file dialog.

## Use

Double-click for the window: choose files, pick a printer, read the analysis,
pick a mode, choose colour and supports, convert. Or drop `.3mf` files straight
onto the icon for the quick path.

Output lands next to the original as `<name> - KEY.3mf`. **Open it in the slicer
Prism names when it finishes.** Each output is a native project for one slicer,
and opening a Snapmaker project in Creality Print fails on Snapmaker's own gcode
macros with an error that reads like a corrupt file.

<details>
<summary><b>All 24 printers, and the slicer each targets</b></summary>

| Make | Printers | Opens in |
|---|---|---|
| **Creality** | K2 (`k2`), K2 Plus (`k2plus`), K2 Pro (`k2pro`), K1C (`k1c`), K1 Max (`k1max`), K1 SE (`k1se`), Ender-3 V3 (`ender3v3`), Ender-3 V3 KE (`ender3v3ke`), Hi (`hi`) | Creality Print |
| **Snapmaker** | U1 (`u1`) **Full Spectrum** | Snapmaker Orca |
| **Bambu Lab** | X1 Carbon (`x1c`), X1E (`x1e`), P1S (`p1s`), P1P (`p1p`), A1 (`a1`), A1 mini (`a1mini`) | Bambu Studio |
| **Prusa** | MK4S (`mk4s`), CORE One (`coreone`) | OrcaSlicer |
| **Elegoo** | Neptune 4 Pro (`neptune4pro`) | OrcaSlicer |
| **Voron** | 2.4 300 (`voron24-300`) | OrcaSlicer |
| **Qidi** | Q1 Pro (`qidiq1pro`) | OrcaSlicer |
| **Sovol** | SV06 (`sv06`) | OrcaSlicer |
| **Anycubic** | Kobra 2 (`kobra2`) | OrcaSlicer |
| **Flashforge** | AD5X (`ad5x`) | OrcaSlicer |

Adding one is a data job, not a code job. See [rebuilding the printer
data](#rebuilding-the-printer-data).
</details>

## What it does

- Rebuilds the project config from a per-slicer template plus the vendor profiles
  for the chosen machine, matched by `compatible_printers`, the same mechanism the
  slicers use.
- Maps filament slots by material, keeping colours and per-object assignments.
- Carries the designer's choices across where the target supports them. Values it
  cannot honour are dropped and named, not silently accepted.
- Reads the mesh. Rounded tops that would print as stair rings get a finer layer
  height, and bed fit and overhangs are reported per object.
- **Checks the mesh and says what is wrong with it.** Holes, edges where the
  surface meets itself, stray shells. Reported, never repaired, because the
  promise below is worth more than the convenience.
- **Accounts for every setting in your file**, so "we left your printer's
  temperatures behind on purpose" cannot be mistaken for "we lost your
  settings", and names the ones costing you time.
- **Never modifies geometry.** Hash-verified on every run, with the plate, object
  and instance structure asserted intact.

Three modes: **speed** for coarser layers, **balanced** as the default,
**quality** for the finest layers plus ironing on large flat tops. A project
authored finer than the mode keeps its finer layer height.

## Supports, only where they are needed

`--supports auto`, or the tick box in the window. It measures every
downward-facing surface: how steep it is, how high it sits, and how far each
ceiling has to reach. Anything narrower than the machine's own bridge limit is
trusted to bridge. What is left gets supports limited to critical regions, and
confined to the build plate when nothing overhangs high enough to need standing
on the model.

    supports on: 8496mm2 reaches further than the 10mm bridge limit, widest span 103mm
      2mm2 of shorter ceiling left to bridge on its own
      limited to critical regions, so nothing is propped up needlessly

A support you did not need costs material, time and a scarred surface, so
nothing is added without a reason you can read. `--supports on` and `off`
override it.

## Which way up

`--orient` says which orientation needs the least support and by how much.
`--orient-apply` turns it. In the window it is a three-way choice.

    object 4 would print better turned:
      as placed   10958mm2 overhang  152.6mm tall  10424mm2 flat on the plate
      turned       4861mm2 overhang  132.6mm tall   1596mm2 flat on the plate

Turning rewrites where the object sits, never the mesh, and keeps its position
in X and Y. Before writing anything it checks the rotation has not mirrored the
part, that it still fits, and that it lands on the plate.

Suggesting is the default on purpose. Least overhang is not the same as best
printed: turning a model changes which faces come out smooth, which way the
layers run and whether a painted model shows its detail. Notice the trade above,
less overhang and shorter, but a much smaller base.

## Full Spectrum colour

The U1 has four nozzles and no mixing chamber, so colour is never a ratio in the
gcode. Snapmaker Orca blends by alternating thin layers until your eye reads them
as one colour, and Prism sets all of that up: it builds the palette, matches your
model's colours to the nearest it can print, paints the file so the slicer
actually blends, and thins the layers so flat faces do not band.

You never mix and you never paint. If you want specific areas in specific
colours, paint in your slicer first and Prism translates what you painted.

### Before you plan a colour scheme

**The gamut is bright and narrow.** Cyan, magenta, yellow and grey, with no white
and no black, cannot reach dark, muted or pale colours. A deep green comes back
as bright teal, a brown as orange, an off-white as mid grey.

![What you ask for, and what you get](docs/colour-gamut.png)

Run `--spectrum-list`, or open the colour picker, and design around what the
printer can actually make. Blending also roughly doubles print time, and the
prime tower uses about 0.11g per tool change.

`--colour-preview` answers this for your actual model before you convert
anything, naming each colour it cannot reach and what it would use instead.

**Blending roughly doubles print time**, and that is not overhead you can tune
away: a full colour cycle has to fit inside one normal layer for your eye to
read it as one colour, so the layers halve. `--spectrum-step` trades that back.
On a real plate: 4 hours at the default, about 2h30 at `0.16`, and the same as
an unblended print at `off`, where flat faces show one filament instead. Curved
and textured surfaces hide coarser bands well; flat tops do not.

**[Full colour and painting guide](docs/COLOUR.md)** covers the rest.

## Bringing in an OBJ or an STL

    prism --printer k2 thing.stl
    prism --printer u1 sculpt.obj --spectrum

Prism reads both and writes a project for your printer, then everything else
applies: supports worked out from the shape, orientation advice, mesh health.
An `.obj` with an `.mtl` beside it keeps its materials as separate objects, so
their colours feed straight into colour matching and Full Spectrum blending.

**It will ask you two things, and it will not guess them.** Neither answer is
in the file, and both produce a result that looks entirely plausible and is
wrong.

    thing.obj carries no units, and 20 x 15 x 40 could be any of these:
        mm        20.0 x     15.0 x     40.0mm
        cm       200.0 x    150.0 x    400.0mm
        inch     508.0 x    381.0 x   1016.0mm
        m      20000.0 x  15000.0 x  40000.0mm   (not a printable size)
      Which did its author work in?

The window asks the same two, as buttons showing what each answer would give
you in millimetres, and will not let you convert until both are answered.

An OBJ or STL is bare numbers, so the same file is a trinket or a monument
depending on what its author had in mind. And printing is Z up while most
modelling tools export Y up, which if taken wrong lays the model on its side
and quietly ruins every overhang and support decision after it. Answer at the
prompt, or pass `--units` and `--up` and never see it.

## When you do not know what a setting does

A Creality K2 profile has 575 settings and the slicer explains almost none of
them, so people change things by rumour. Ask instead:

    prism --explain infill --printer k2
    prism --explain "z distance"
    prism --explain seam

You get what it is, what moving it up or down actually does, when to change it,
what it costs you, and with `--printer`, the value on your machine and what it
will accept.

And when something has gone wrong, describe it:

    prism --fix "failed at 80%" yourfile.3mf
    prism --fix stringing

You get the likely causes, most common first, and the settings involved. Where
Prism can check your file it does, which is the point: a general answer about
failed prints is a guess, and "object 8 has 151mm2 of overhang and supports are
OFF" is not. Where it cannot see the cause, it says so rather than guessing.
Stringing is damp filament, and no setting in your project file will fix it.

Asking why a print is slow also checks whether the file contains the preset it
claims. A project records which settings its owner deliberately changed;
anything else is supposed to be the named preset's own value, and when it is
not, the file says one thing and contains another with no sign of it in any
slicer. Prism shipped exactly that fault for two months, so it now looks for it
in yours.

## Settings

Every printer gets three standing defaults, applied after the source's own
settings because they are your preferences for your machine rather than the
designer's guess about someone else's:

| Setting | Value | Why |
|---|---|---|
| `sparse_infill_pattern` | `gyroid` | Isotropic, no crossings |
| `support_interface_top_layers` | `3` | Cleaner surface under supports |
| `support_top_z_distance` | `0.25` | Releases without tearing |

One printer has a fourth. The Snapmaker U1 runs PLA on its textured plate at
50C rather than the 65C Snapmaker specifies, which is warm enough to splay the
bottom few layers outwards into an elephant foot. It is a declared override
like the three above: the panel shows it as a Prism value, the run output names
it, and `--keep-source` or `--set textured_plate_temp=65` restores Snapmaker's.

Bed temperature is two settings, the bed and the first layer, and 23 of the 24
printers here ship them equal. So changing one moves the other, which matters
because the first layer is where a hot plate does the damage. A profile that
ships them apart on purpose, as the Flashforge AD5X does at 5C cooler, keeps
its own offset.

Anything they override is named in the run output. `--keep-source` turns them off.

Settings you arrive at are worth keeping. `--save-prefs` remembers this run's
choices and every later run applies them, announced rather than silently. An
explicit flag always beats a saved preference, and one that a given printer
cannot accept is dropped with a message instead of refusing the job.

Open **Advanced settings** in the window to change these and more. Every setting
carries an **i** giving the slicer's own name for it and what changing it
actually does, because most people meet these settings without ever being told.

![Advanced settings](docs/advanced.png)

The three fans are sliders: part cooling, the auxiliary chamber fan, and a
separate speed used only over overhangs and bridges. Turning the main fan down
also caps its minimum, since the cooling logic ramps between the two.

## Command line

    prism --list                                  every printer and its slicer
    prism --printer <key> --report file.3mf       analysis, writes nothing
    prism --printer <key> --mode quality file.3mf
    prism --interactive file.3mf

    --supports auto|on|off       supports only where the model needs them
    --orient                     which way up needs the least support
    --orient-apply               and turn it
    --spectrum                   Full Spectrum blending (U1)
    --spectrum-list              every colour the printer can make, with ids
    --spectrum-colour C          #RRGGBB, a palette id, or a name like Teal
    --spectrum-step MM | off     layer height in painted zones
    --single [#RRGGBB]           one filament for the whole model
    --fan / --aux-fan / --overhang-fan N
    --infill / --infill-density / --walls / --top-layers / --bottom-layers
    --interface-layers / --top-z / --support-style / --seam / --brim
    --units mm|cm|m|inch         units an imported .obj or .stl was drawn in
    --up z|y                     which axis is up in one
    --explain SETTING            what a setting does and how to use it
    --fix SYMPTOM                what causes a problem, checked against your file
    --colour-preview             what this model's colours become, before converting
    --set KEY=VALUE              any other profile key, repeatable
    --list-settings              what you can change on this printer
    --save-prefs                 remember this run's settings as your defaults
    --show-prefs / --no-prefs    list them, or ignore them for one run
    --keep-source                ignore Prism's standing defaults
    --dome off|H                 override the rounded-top layer height
    --out PATH

Values are checked against what the printer supports, so a wrong one is refused
with the allowed list rather than written into a file the slicer will reject.

In a release build the command is `Prism.exe --engine ...` on Windows and
`prism --engine ...` on Linux. From source it is
`python3 engine/optimise3mf.py`.

## Build from source

    git clone https://github.com/4bsxhwr68n-debug/prism.git
    cd prism
    ./macos/build.sh        # ~/Desktop/Prism.app
    ./windows/build.sh      # ~/Desktop/Prism (Windows).zip
    ./linux/build.sh        # ~/Prism-Linux.tar.gz

`./release.sh v1.0.4` builds everything at once. The macOS script needs macOS,
because `osacompile` and `codesign` are macOS tools. The engine also runs on its
own: `python3 engine/optimise3mf.py --interactive yourfile.3mf`.

## Rebuilding the printer data

`engine/data/` is baked from the vendor profiles inside installed slicers. After
a major slicer upgrade run `python3 engine/bake.py` with those slicers installed.
It writes `out_v2/`; copy `printers/` and `index.json` into `engine/data/`.

## If something goes wrong

**"Prism is damaged and can't be opened"** on macOS. You are on a release before
v1.0.6, which is when the app was notarised. Download the current one. If you
would rather open the old copy, macOS 15 and later say this about any unsigned
download, and the way through is System Settings, Privacy and Security, Open
Anyway, because right-click and Open was removed.

**The file opens with an error about custom gcode.** It is in the wrong slicer.
Check the one Prism named when it converted.

**The blend prints as one colour.** Check the filament table after slicing.
Balanced usage across two filaments with hundreds of tool changes means it
worked. Almost everything on one filament means the model reached the slicer
unpainted, which is worth reporting.

**Colours look wrong.** The gamut has no dark end. Run `--spectrum-list`.

**No file dialog on Linux.** Install `zenity`, `kdialog` or `yad`, or pass files
on the command line.

## Contributing

Issues and pull requests welcome, particularly new printer profiles and any
slicer that rejects a converted file. Attach the source `.3mf` where you can:
almost everything here is a file-format bug and they are hard to guess at.

## Support

Free, no accounts, no telemetry. If it saved you a failed print,
[buy me a coffee](https://buymeacoffee.com/prismprints).

## Licence

AGPL-3.0. Parts of the Full Spectrum support derive from Snapmaker Orca, which is
AGPL-3.0, and `engine/mixer.py` is a transliteration of an MIT-licensed pigment
model. [NOTICE.md](NOTICE.md) records exactly what came from where.

Not affiliated with or endorsed by any printer manufacturer.
