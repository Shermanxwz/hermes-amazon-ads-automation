"""Hermes Amazon Ads deterministic closed-loop control plane."""

from .extension_registry import install_extensions as _install_extensions

__version__ = "3.3.0"

_install_extensions()

del _install_extensions
