"""Neuron entrypoints.

``miner.py`` and ``validator.py`` are thin wrappers: they build the neuron,
turn a configuration or preflight failure into a one-line message and a non-zero
exit, and hand control to the run loop. Everything else lives in the package.
"""
