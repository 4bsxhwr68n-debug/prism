"""The one place Prism's version is written down.

It lives in the source rather than being stamped in at build time, so the
repository always states which version it is, and `release.sh` refuses a tag
that disagrees with it. A number injected by the build is a number nobody can
check by reading the code, and the update check below is only as trustworthy
as the version it compares against.
"""

VERSION = '1.2.0'

# Where the update check looks. Public, unauthenticated, read only.
REPO = '4bsxhwr68n-debug/prism'
RELEASES_URL = 'https://api.github.com/repos/%s/releases/latest' % REPO
DOWNLOAD_PAGE = 'https://github.com/%s/releases/latest' % REPO


def parts(v):
    """'v1.2.0' and '1.2.0' both become (1, 2, 0).

    Anything after the numbers is ignored rather than guessed at, so a tag like
    1.2.0-rc1 compares equal to 1.2.0 instead of throwing. Prism has never
    published one, and the day it does, an update prompt is not the place to
    discover that the parser was optimistic."""
    out = []
    for chunk in str(v or '').lstrip('vV').split('.'):
        digits = ''
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        out.append(int(digits))
    return tuple(out) or (0,)


def is_newer(candidate, current=VERSION):
    """Is `candidate` a later version than `current`."""
    a, b = parts(candidate), parts(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b
