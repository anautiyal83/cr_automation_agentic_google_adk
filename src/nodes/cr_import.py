"""Import helper — loads modules from cr_automation-agentic without src package conflicts.

The key challenge: both projects have a `src/` package. When we import
a module from cr_automation-agentic, its internal `from src.agents import X`
must resolve to cr_automation-agentic's src, not ours.

Solution: Temporarily swap sys.path so cr_automation-agentic's root is first,
and also ensure its `src` package is registered in sys.modules before loading.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

CR_AUTO_ROOT = Path("D:/my_workspace/cr_automation-agentic")

# Cache loaded modules
_module_cache: dict[str, Any] = {}
_initialized = False


def clear_cache():
    """Clear module cache — use when cr_automation-agentic code changes."""
    global _initialized
    _module_cache.clear()
    _initialized = False
    # Also remove from sys.modules
    to_remove = [k for k in sys.modules if k.startswith("src.agents") or k.startswith("src.core") or k.startswith("src.parsers")]
    for k in to_remove:
        del sys.modules[k]


def _ensure_cr_auto_importable():
    """Make cr_automation-agentic's src package importable via normal imports.

    This is called once to set up the import path so that modules inside
    cr_automation-agentic can do `from src.agents import X` and get their
    own src package, not ours.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    cr_root_str = str(CR_AUTO_ROOT)

    # Save our project's src module
    our_src = sys.modules.get("src")

    # Temporarily put cr_automation-agentic first in sys.path
    if cr_root_str in sys.path:
        sys.path.remove(cr_root_str)
    sys.path.insert(0, cr_root_str)

    # Force-load cr_automation-agentic's src package
    # This replaces our src in sys.modules temporarily
    cr_src_init = CR_AUTO_ROOT / "src" / "__init__.py"
    if cr_src_init.exists():
        spec = importlib.util.spec_from_file_location("src", str(cr_src_init),
            submodule_search_locations=[str(CR_AUTO_ROOT / "src")])
        if spec and spec.loader:
            cr_src_mod = importlib.util.module_from_spec(spec)
            sys.modules["src"] = cr_src_mod
            spec.loader.exec_module(cr_src_mod)

    # Now load all src sub-packages from cr_automation-agentic
    for sub in ["agents", "core", "core.models", "core.retry", "core.intermediate_repr",
                "core.events", "core.config", "storage", "storage.db", "storage.artifact_store",
                "testing", "testing.report"]:
        full = f"src.{sub}"
        if full not in sys.modules:
            sub_path = CR_AUTO_ROOT / "src" / sub.replace(".", "/")
            py_path = sub_path.with_suffix(".py")
            if not py_path.exists():
                py_path = sub_path / "__init__.py"
            if py_path.exists():
                spec = importlib.util.spec_from_file_location(full, str(py_path),
                    submodule_search_locations=[str(sub_path)] if sub_path.is_dir() else None)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[full] = mod
                    try:
                        spec.loader.exec_module(mod)
                    except Exception:
                        pass  # Some modules may fail on first load, will retry on actual use


def import_cr_module(module_path: str) -> Any:
    """Import a module from cr_automation-agentic by relative path.

    Args:
        module_path: Dot-separated path relative to cr_automation-agentic,
                     e.g. "src.agents.doc_parser_agent"

    Returns:
        The loaded module.
    """
    _ensure_cr_auto_importable()

    if module_path in _module_cache:
        return _module_cache[module_path]

    # Prefer the version already in sys.modules (ensures single instance for shared state)
    if module_path in sys.modules:
        mod = sys.modules[module_path]
        _module_cache[module_path] = mod
        return mod

    file_path = CR_AUTO_ROOT / module_path.replace(".", "/")
    py_path = file_path.with_suffix(".py")
    if not py_path.exists():
        py_path = file_path / "__init__.py"
    if not py_path.exists():
        raise ImportError(f"Cannot find {module_path} at {py_path}")

    # Load the module using the standard import path (cr_auto root is in sys.path)
    spec = importlib.util.spec_from_file_location(module_path, str(py_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create spec for {module_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_path] = mod
    spec.loader.exec_module(mod)
    _module_cache[module_path] = mod
    return mod


def get_agent_class(agent_module: str, class_name: str) -> type:
    """Import and return an agent class from cr_automation-agentic.

    Args:
        agent_module: e.g. "src.agents.doc_parser_agent"
        class_name: e.g. "DocParserAgent"
    """
    mod = import_cr_module(agent_module)
    return getattr(mod, class_name)
