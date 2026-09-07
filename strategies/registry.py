"""
Auto-discovery registry. Drop any .py file into strategies/ that defines a
subclass of Strategy, and it's picked up automatically — no edits needed
here or anywhere else in core.

This file is the one exception to "adding a strategy never touches core
files" — it IS the mechanism, not something you edit per-strategy.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path

from strategies.base import Strategy

log = logging.getLogger("strategies.registry")

_EXCLUDE_MODULES = {"base", "registry", "__init__"}


def discover_strategies() -> dict[str, Strategy]:
    """Scan strategies/*.py, import each, instantiate every Strategy
    subclass found, keyed by its `.name` attribute."""
    registry: dict[str, Strategy] = {}
    package_dir = Path(__file__).resolve().parent

    for _, module_name, is_pkg in pkgutil.iter_modules([str(package_dir)]):
        if is_pkg or module_name in _EXCLUDE_MODULES:
            continue
        try:
            module = importlib.import_module(f"strategies.{module_name}")
        except Exception:
            log.exception("Failed to import strategies/%s.py — skipping", module_name)
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is Strategy or not issubclass(obj, Strategy):
                continue
            if obj.__module__ != module.__name__:
                continue  # skip re-exported/imported classes from other modules
            try:
                instance = obj()
            except Exception:
                log.exception("Failed to instantiate strategy class %s in %s", obj.__name__, module_name)
                continue
            if instance.name in registry:
                log.warning("Duplicate strategy name '%s' — overwriting", instance.name)
            registry[instance.name] = instance
            log.info("Registered strategy: %s (%s)", instance.name, module_name)

    return registry
