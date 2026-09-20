# Attribution

Prism interoperates with slicer project files, and two parts of it are derived
from other people's work. Both are named here because they should be.

## Snapmaker Orca (AGPL-3.0)

The Full Spectrum support reads and writes structures defined by Snapmaker Orca,
a fork of OrcaSlicer: <https://github.com/Snapmaker/OrcaSlicer>

Written against tag `v2.3.6`. Specifically:

- `mixed_filament_definitions` row grammar, from
  `src/libslic3r/MixedFilament.cpp` (`MixedFilamentManager::serialize_custom_entries`
  and `parse_row_definition`).
- Virtual filament numbering, from `src/libslic3r/MixedFilament.hpp`.
- The blend preview chain reproduced in `engine/optimise3mf.py`
  (`pair_ratios`, `blend_multi`, `mix_rgb`), from `effective_pair_preview_ratios`,
  `build_effective_pair_preview_sequence`, `blend_display_color_from_sequence`
  and `blend_color_multi`.
- The `paint_color` per-triangle encoding, from
  `src/libslic3r/TriangleSelector.cpp` (`TriangleSelector::serialize`) and
  `src/libslic3r/Model.cpp` (`FacetsAnnotation::get_triangle_as_string` and
  `set_triangle_from_string`).

Because that work is derived from AGPL-3.0 source, Prism is AGPL-3.0 too.

## FilamentMixer (MIT)

`engine/mixer.py` is a mechanical transliteration of
`src/libslic3r/filament_mixer_model.h`: a degree-4 polynomial regression trained
to approximate Mixbox pigment mixing.

> MIT License. Copyright (c) 2026 Justin Hayes.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

This library does not include Mixbox source code, binaries, or data files.

## Printer profiles

`engine/data/` is baked from the vendor profiles shipped inside installed
slicers. Rebuild it yourself with `engine/bake.py`; it is regenerated data, not
authored content.

## Not affiliated

Prism is not affiliated with or endorsed by Snapmaker, Creality, Bambu Lab,
Prusa, or anyone else whose printers it supports.
