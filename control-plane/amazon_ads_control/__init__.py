"""Hermes Amazon Ads deterministic closed-loop control plane."""

from .closed_loop import install as _install_closed_loop
from .closed_loop_fixes import install as _install_closed_loop_fixes
from .strategy_hardening import install as _install_strategy_hardening

__version__ = "3.0.0"

_install_closed_loop()
_install_closed_loop_fixes()
_install_strategy_hardening()

from .report_evidence_hardening import install as _install_report_evidence_hardening
from .callback_hardening import install as _install_callback_hardening
from .task_hardening import install as _install_task_hardening
from .api_extension import install as _install_api_extension
_install_report_evidence_hardening()
_install_callback_hardening()
_install_task_hardening()
_install_api_extension()

del _install_closed_loop, _install_closed_loop_fixes, _install_strategy_hardening
del _install_report_evidence_hardening, _install_callback_hardening
del _install_task_hardening, _install_api_extension
