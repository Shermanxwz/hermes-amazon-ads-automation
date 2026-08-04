"""Hermes Amazon Ads deterministic closed-loop control plane."""

from .closed_loop import install as _install_closed_loop
from .strategy_hardening import install as _install_strategy_hardening

__version__ = "3.0.0"

_install_closed_loop()
_install_strategy_hardening()
del _install_closed_loop, _install_strategy_hardening
