"""Fetch what `outcomes.build` needs: the I/O half of outcome collection.

GitHub is read through `gh`, so it uses whatever login `gh` has. The project's git history is
read with `git`. Package registries (PyPI, npm) are read anonymously with `urllib`; only package
names are sent. A registry or repository that can't be read gives that package an `error`, not a
failed collection.
"""

import subprocess
from collections.abc import Callable
from typing import Any

Fetch = Callable[[str], Any]


class CollectError(Exception):
    pass


def gh(*args: str) -> str:
    result = subprocess.run(['gh', *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise CollectError(f'gh {" ".join(args[:2])} failed: {result.stderr.strip()}')
    return result.stdout
