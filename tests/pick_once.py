"""Call the file picker once and print what it returned.

A file rather than `python -c "..."`, because passing that string through
PowerShell's argument handling truncated it to `import` and the picker never
ran at all. The z-order test then measured a dialog left over from an earlier
step and reported a failure that had nothing to do with the code under test.
A script on disk has no quoting layer to get wrong.
"""

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engine'))

import gui                                          # noqa: E402

print(gui.pick_files())
