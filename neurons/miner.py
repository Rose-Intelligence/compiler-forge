#!/usr/bin/env python3
"""CompilerForge miner entrypoint.

    python neurons/miner.py \
        --netuid 1 \
        --wallet.name miner --wallet.hotkey default \
        --subtensor.network finney \
        --artifact.image ghcr.io/you/my-optimizer

The image must already be pushed: the neuron commits its sha256 digest, and a
digest that no registry can serve is a commitment to nothing.

Run `python neurons/miner.py --help` for the full argument list, and see
docs/miner.md for the artifact contract and a walkthrough of building one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compilerforge.miner.neuron import Miner  # noqa: E402
from compilerforge.utils.logging import configure, logger  # noqa: E402


def main() -> int:
    configure()
    try:
        miner = Miner()
    except (RuntimeError, ValueError) as exc:
        logger.error(str(exc))
        return 1

    miner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
