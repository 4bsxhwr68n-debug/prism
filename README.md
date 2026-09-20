# Prism

**Retarget any 3MF. Blend any colour.**

Prism takes a 3MF project made for one printer and retargets it to another, so
it opens as a proper native project instead of a pile of broken settings. It
covers 24 machines across Creality, Snapmaker, Bambu Lab, Prusa, Elegoo, Voron,
Qidi, Sovol, Anycubic and Flashforge.

On the Snapmaker U1 it also does Full Spectrum colour. Load cyan, magenta,
yellow and grey, and Prism works out the blends, matches your model's colours to
the closest one the printer can actually produce, and paints the file so the
slicer prints them. You never mix by hand and you never paint.

![The Prism window](docs/header.png)

## Install

**macOS.** Download the release, drag `Prism.app` where you like, double-click
it. On first run macOS may say it is from an unidentified developer: right-click
the app and choose Open. Nothing to install, it uses the Python already on your
Mac.

**Windows.** Download `Prism (Windows).zip`, unzip it anywhere, double-click
`Prism.bat`. You need Python 3 from [python.org](https://www.python.org/downloads/)
or the Microsoft Store. No packages, no pip.

**Build the Mac app from source.** `./macos/build.sh`

## Use

Double-click for the window: choose files, pick a printer, read the analysis,
pick a mode, pick a colour, convert. Output lands next to the original as
`<name> - KEY.3mf`.

Or drop `.3mf` files straight onto the app icon for the quick path.

![Choosing a colour](docs/palette.png)

## What it actually does

- Rebuilds the project config from a proven per-slicer template plus the vendor
  profiles for the chosen machine, matched by `compatible_printers`, the same
  mechanism the slicers use.
- Maps filament slots by material type, preserving colours and per-object
  extruder assignments.
- Carries designer intent across where it is valid on the target: walls, infill,
  supports, brim, seam. Enum values are checked against the slicer binaries, so
  a setting the target cannot honour is dropped rather than silently accepted.
- Looks at the mesh. Rounded tops that would print as stair rings get a finer
  per-object layer height. Bed fit and overhangs are reported per object.
- Never modifies geometry. It is hash-verified on every run, and the plate,
  object and instance structure is asserted intact.

Three modes: **speed** for coarser layers, **balanced** as the sensible default,
**quality** for the finest layers plus ironing on large flat tops. A project
authored finer than the mode keeps its finer layer height; only speed coarsens.

## Full Spectrum

The U1 has four independent nozzles and no mixing chamber, so colour is never a
ratio in the gcode. Snapmaker Orca blends by alternating thin layers of two
filaments until the eye reads them as one colour. Prism sets that up:

- Loads the four semi-translucent Full Spectrum filaments and generates the
  palette: every pair at three blend strengths, 18 mixes plus the 4 solids.
- Matches colour in CIE Lab, not RGB, because RGB distance picks visibly wrong
  blends. The blend colour comes from the same pigment model the slicer uses, so
  what Prism promises is what the slicer shows. It is subtractive: cyan plus
  yellow makes green, not grey.
- Paints the model. Assigning a part to a blend is not enough on its own,
  because the slicer only blends painted geometry. A model that is already
  painted is recoloured rather than painted over, so multi-colour artwork
  survives.
- Halves the layer height so a full colour cycle fits inside one nominal layer.
  Without that, flat faces print one filament neat and band visibly.

Honest edges: the gamut has no dark end, so a near-black target gets the closest
available colour and the run says so. Blending roughly doubles print time and
the prime tower uses about 0.11g per tool change. Use the real semi-translucent
filaments; opaque ones stripe on shallow slopes.

## Command line

    python3 engine/optimise3mf.py --list
    python3 engine/optimise3mf.py --printer <key> --report file.3mf
    python3 engine/optimise3mf.py --printer <key> --mode quality file.3mf
    python3 engine/optimise3mf.py --interactive file.3mf

    --single [#RRGGBB]          one filament for the whole model
    --spectrum                  Full Spectrum blending (U1)
    --spectrum-list             every colour the printer can make, with ids
    --spectrum-colour C         #RRGGBB, a palette id, or a name like Teal
    --spectrum-step MM | off    layer height in painted zones
    --spectrum-biases 25,50,75  blend strengths per pair
    --dome off|H                override the rounded-top layer height
    --out PATH                  explicit output path

## Rebuilding the printer data

`engine/data/` is baked from the vendor profiles inside installed slicers. After
a major slicer upgrade, run `python3 engine/bake.py` with those slicers
installed; it writes `out_v2/`, and you copy `printers/` and `index.json` into
`engine/data/`.

## Support

Prism is free and there are no accounts, licence keys or telemetry. If it saved
you a failed print, [buy me a coffee](https://buymeacoffee.com/SET-ME).

## Licence

AGPL-3.0. Parts of the Full Spectrum support are derived from Snapmaker Orca,
which is AGPL-3.0, and `engine/mixer.py` is a transliteration of an MIT-licensed
pigment model. See [NOTICE.md](NOTICE.md) for exactly what came from where.

Not affiliated with or endorsed by Snapmaker, Creality, Bambu Lab, Prusa, or any
other printer manufacturer.
