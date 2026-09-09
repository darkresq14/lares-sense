#!/usr/bin/env python3
"""
Working Config persistence (issue #10).

The Working Config — the current full cfg file, the single source of truth
for what the sensor was last told to do — lives on the Pi in a data dir
**outside the repo**. The repo keeps only the pristine vendor template
(ADR-0004: "the full generated cfg file persisted on the Pi ... not in the
repo"). On first run the Working Config is seeded from that template;
afterwards every load prefers the persisted copy, so values survive process
restarts and power blips.

The default data dir is resolved once at import: ``SETUP_TOOL_DATA_DIR``
env var wins (tests, systemd units), otherwise the platform's shared-data
location.
"""

import os
import tempfile
from pathlib import Path

#: Name of the persisted Working Config inside the data dir.
WORKING_CONFIG_NAME = 'working_config.cfg'

#: The pristine vendor template in the repo (the seeding source).
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / \
    'xwr6843_aop_3d_people_track_6m.cfg'


def default_data_dir():
    """Pi-local data dir, never inside the repo.

    ``SETUP_TOOL_DATA_DIR`` overrides; otherwise XDG data home on Linux
    (the Pi) and %LOCALAPPDATA% on Windows (dev machines).
    """
    env = os.environ.get('SETUP_TOOL_DATA_DIR')
    if env:
        return Path(env)
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData'))
        return Path(base) / 'Lares' / 'setup_tool'
    base = os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))
    return Path(base) / 'lares' / 'setup_tool'


class WorkingConfigStore:
    """Load/save the Working Config cfg file in one data dir.

    Small, synchronous, atomic: saves write a temp file and rename over the
    target, so a power cut mid-save can never leave a truncated cfg.
    """

    def __init__(self, data_dir=None, template_path=TEMPLATE_PATH):
        self._dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self._template_path = Path(template_path)
        self._path = self._dir / WORKING_CONFIG_NAME

    @property
    def path(self):
        """Full path of the persisted Working Config."""
        return self._path

    def load(self):
        """Return the Working Config text, seeding it on first run.

        If the persisted file exists it wins (values saved in earlier
        sessions). Otherwise the vendor template is copied verbatim into
        the data dir and that text is returned.
        """
        if self._path.exists():
            with open(self._path, encoding='ascii', newline='') as f:
                return f.read()
        with open(self._template_path, encoding='ascii', newline='') as f:
            text = f.read()
        self.save(text)
        return text

    def quarantine(self):
        """Move an unreadable Working Config aside; return the new name.

        Called by the host when the persisted cfg fails to parse, so the
        operator's broken file survives for inspection while the next
        :meth:`load` re-seeds from the template.
        """
        kept_as = self._path.with_suffix(self._path.suffix + '.corrupt')
        self._path.replace(kept_as)
        return kept_as.name

    def save(self, text):
        """Atomically persist the full generated cfg text."""
        self._dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._dir),
                                        prefix='.working_config.',
                                        suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='ascii', newline='') as f:
                f.write(text)
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
