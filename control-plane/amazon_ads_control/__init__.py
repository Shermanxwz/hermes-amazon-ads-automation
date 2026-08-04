"""Hermes Amazon Ads deterministic closed-loop control plane."""

from .closed_loop import install as _install_closed_loop

__version__ = "3.0.0"

_install_closed_loop()
del _install_closed_loop
