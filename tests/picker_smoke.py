"""Can the file picker be called at all.

This is not "does the PowerShell parse", which is a separate check and which
passed happily for three releases while the picker was completely broken on
Windows. The fault was a missing `import tempfile`, on a line inside the
Windows-only branch, two lines before PowerShell was ever involved. No Mac or
Linux run reaches that branch, `py_compile` does not look for undefined names,
and the PowerShell check extracted the script and ran it directly without ever
calling the Python wrapped around it. So every gate we had said yes.

What this asks instead is whether `pick_files()` itself runs far enough to put
a dialog on the screen. On a runner with nobody at it a shown dialog blocks
forever, so being still alive after the wait is the pass: it means every line
before `ShowDialog` executed. An exception, by contrast, arrives in
milliseconds.

Run it on any platform. It is only meaningful on Windows, where the branch that
broke actually runs, but it is harmless elsewhere.
"""

import os
import sys
import threading

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engine'))

import gui                                          # noqa: E402

WAIT_SECONDS = 25

box = {}


def call():
    try:
        box['returned'] = gui.pick_files()
    except BaseException as exc:                    # noqa: BLE001
        box['raised'] = '%s: %s' % (type(exc).__name__, exc)


t = threading.Thread(target=call, daemon=True)
t.start()
t.join(WAIT_SECONDS)

if 'raised' in box:
    sys.exit('pick_files raised before showing anything: %s' % box['raised'])

if 'returned' in box:
    files, note = box['returned']
    # A note is a picker that could not run and said so, which is a working
    # code path. It is only a failure if it names the kind of fault this test
    # exists for.
    print('pick_files returned: files=%r note=%r' % (files, note))
    for bad in ('NameError', 'AttributeError', 'ImportError', 'UnboundLocalError'):
        if note and bad in note:
            sys.exit('pick_files failed on a programming error: %s' % note)
    sys.exit(0)

print('pick_files is still inside the dialog after %ds, '
      'which means every line before it ran' % WAIT_SECONDS)
