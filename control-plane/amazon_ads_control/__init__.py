"""Hermes Amazon Ads deterministic control plane."""

from .result_replay import install as _install_result_replay

__version__ = "2.1.0"

_install_result_replay()
del _install_result_replay
