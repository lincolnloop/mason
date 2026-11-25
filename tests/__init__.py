import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))

# mason keeps settings, run state, clones and skills in the developer's home directory
# (paths.py). Tests must never read or write the real ones, so every XDG base directory mason uses
# points into one temporary directory for the whole run, subprocesses included. A new directory
# in paths.py needs its variable here.
HOME = Path(tempfile.mkdtemp(prefix='mason-tests-'))
atexit.register(shutil.rmtree, HOME, ignore_errors=True)
for variable, sub in (
    ('XDG_CONFIG_HOME', 'config'),
    ('XDG_STATE_HOME', 'state'),
    ('XDG_CACHE_HOME', 'cache'),
    ('XDG_DATA_HOME', 'data'),
):
    os.environ[variable] = str(HOME / sub)
