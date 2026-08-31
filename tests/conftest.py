"""Make bin/mailcommon.py importable.

bin/ holds executables without a .py suffix, so it is not a package. The module
under test is the one file in there that is a plain library.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
