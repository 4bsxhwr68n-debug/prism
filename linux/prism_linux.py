"""Linux entry point, bundled by PyInstaller into the prism binary.

One binary serving three jobs, dispatched on argv:

    prism                    open the app window
    prism a.3mf b.3mf        files dropped on the icon, console journey
    prism --engine ...       internal: the engine, re-entered by the GUI

The third exists because inside a frozen bundle sys.executable is this exe
rather than a Python interpreter, so the GUI cannot shell out to a .py file.
"""
import os
import sys


def main():
    args = sys.argv[1:]
    if args and args[0] == '--engine':
        sys.argv = [sys.argv[0]] + args[1:]
        import optimise3mf
        optimise3mf.main()
        return
    if args:
        # dropped on the icon: straight into the interactive console flow
        sys.argv = [sys.argv[0], '--interactive'] + args
        import optimise3mf
        optimise3mf.main()
        input('\nPress Enter to close.')
        return
    import gui
    gui.main()


if __name__ == '__main__':
    main()
