#!/usr/bin/env python3
"""CompilerForge validator entrypoint.

    python neurons/validator.py \
        --netuid 1 \
        --wallet.name validator --wallet.hotkey default \
        --subtensor.network finney \
        --corpus.dir ./corpus --corpus.snapshot cf-corpus-2026.08

Run `python neurons/validator.py --help` for the full argument list, and see
docs/validator.md for hardware requirements and the operational checklist.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compilerforge.utils.logging import configure, logger  # noqa: E402
from compilerforge.validator.neuron import Validator  # noqa: E402


def main() -> int:
    configure()
    try:
        validator = Validator()
    except (RuntimeError, ValueError) as exc:
        # Preflight and configuration failures have a specific fix, not a stack
        # trace. Print the fix and exit non-zero so a supervisor notices.
        logger.error(str(exc))
        return 1

    validator.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
