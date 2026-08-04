"""Hermes Amazon Ads deterministic control plane."""

__version__ = "2.1.0"

# Install the replay-safe result callback before consumers import ControlService.
from .result_replay import install as _install_result_replay

_install_result_replay()
del _install_result_replay
