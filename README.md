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

**[Full colour and painting guide](docs/COLOUR.md)** covers the rest.

## Settings

Every printer gets three standing defaults, applied after the source's own
settings because they are your preferences for your machine rather than the
designer's guess about someone else's:

| Setting | Value | Why |
|---|---|---|
| `sparse_infill_pattern` | `gyroid` | Isotropic, no crossings |
| `support_interface_top_layers` | `3` | Cleaner surface under supports |
| `support_top_z_distance` | `0.25` | Releases without tearing |

Anything they override is named in the run output. `--keep-source` turns them off.

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
    --set KEY=VALUE              any other profile key, repeatable
    --list-settings              what you can change on this printer
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
