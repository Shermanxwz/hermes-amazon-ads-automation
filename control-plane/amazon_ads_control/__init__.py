"""Hermes Amazon Ads full-managed, deterministic ACOS control plane."""

from .extension_registry import install_extensions as _install_extensions

__version__ = "4.1.0"

_install_extensions()

del _install_extensions
