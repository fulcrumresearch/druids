"""Allow `python -m druids_runtime /tmp/runtime_config.json`."""

from __future__ import annotations

import asyncio

from druids_runtime import main


asyncio.run(main())
