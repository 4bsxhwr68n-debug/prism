"""Is there a newer Prism than this one.

Deliberately small and deliberately passive. Prism asks GitHub what the latest
release is, compares it with the version in version.py, and if there is a newer
one it says so and links to it. It does not download anything, it does not
replace itself, and it never runs anything it fetched.

That restraint is the design, not an unfinished edge. Replacing a running
.app risks the notarised signature it was just checked against, Windows will
not let a running .exe overwrite itself without a helper process, and an
application that downloads and executes binaries is the exact shape antivirus
exists to object to. A link and a sentence cost none of that.

It is also allowed to fail. Offline, rate limited, GitHub down, a proxy in the
way: every one of those ends as "no answer", which is not an error worth
showing anybody. The app works perfectly well without knowing.
"""

import json
import os
import time
import urllib.error
import urllib.request

from prismversion import (DOWNLOAD_PAGE, RELEASES_URL, VERSION, is_newer)

# Unauthenticated api.github.com allows 60 requests an hour per address. One
# per launch is nowhere near that, but a cache means opening the window five
# times while testing something does not spend five of them, and it means a
# machine that opens Prism all day asks roughly twice.
CACHE_HOURS = 6
TIMEOUT = 6


def _cache_path():
    base = (os.environ.get('PRISM_PREFS')
            or os.environ.get('XDG_CONFIG_HOME')
            or os.path.join(os.path.expanduser('~'), '.config'))
    if base.endswith('.json'):
        base = os.path.dirname(base)
    return os.path.join(base, 'prism-update.json')


def _read_cache():
    try:
        with open(_cache_path(), encoding='utf-8') as fh:
            d = json.load(fh)
        if time.time() - float(d.get('at', 0)) < CACHE_HOURS * 3600:
            return d.get('tag')
    except Exception:
        pass
    return None


def _write_cache(tag):
    try:
        path = _cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'at': time.time(), 'tag': tag}, fh)
    except Exception:
        pass            # a cache that cannot be written is not a problem


def latest_tag(force=False):
    """The newest published tag, or None if we could not find out."""
    if not force:
        cached = _read_cache()
        if cached:
            return cached
    req = urllib.request.Request(
        RELEASES_URL,
        headers={'Accept': 'application/vnd.github+json',
                 'User-Agent': 'Prism/%s' % VERSION})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            tag = json.loads(r.read().decode('utf-8')).get('tag_name')
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None
    if tag:
        _write_cache(tag)
    return tag


def check(force=False):
    """What the window shows. Always a dict, never raises.

    `newer` is only ever True when we actually heard back and the answer was
    higher than what is running. Not knowing is reported as not knowing, so an
    offline launch cannot produce an update prompt for a version that may not
    exist."""
    if os.environ.get('PRISM_NO_UPDATE_CHECK'):
        return {'current': VERSION, 'latest': None, 'newer': False,
                'checked': False, 'url': DOWNLOAD_PAGE,
                'note': 'Update checking is switched off here.'}
    tag = latest_tag(force=force)
    if not tag:
        return {'current': VERSION, 'latest': None, 'newer': False,
                'checked': False, 'url': DOWNLOAD_PAGE,
                'note': 'Could not reach GitHub to check.'}
    return {'current': VERSION, 'latest': tag.lstrip('vV'),
            'newer': bool(is_newer(tag)), 'checked': True,
            'url': DOWNLOAD_PAGE, 'note': ''}
