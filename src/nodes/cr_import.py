"""Import helper — loads modules from cr_automation-agentic without src package conflicts."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

CR_AUTO_ROOT = Path("D:/my_workspace/cr_automation-agentic")

# Cache loaded modules
_module_cache: dict[str, Any] = {}


def import_cr_module(module_path: str) -> Any:
    """Import a module from cr_automation-agentic by relative path.

    Args:
        module_path: Dot-separated path relative to cr_automation-agentic,
                     e.g. "src.agents.doc_parser_agent"

    Returns:
        The loaded module.
    """
    if module_path in _module_cache:
        return _module_cache[module_path]

    file_path = CR_AUTO_ROOT / module_path.replace(".", "/")
    # Try as .py file first, then as package __init__.py
    py_path = file_path.with_suffix(".py")
    if not py_path.exists():
        py_path = file_path / "__init__.py"
    if not py_path.exists():
        raise ImportError(f"Cannot find {module_path} at {py_path}")

    # Ensure cr_automation-agentic root is in sys.path for the module's own imports
    cr_root_str = str(CR_AUTO_ROOT)
    if cr_root_str not in sys.path:
        sys.path.insert(0, cr_root_str)

    module_name = f"cr_auto.{module_path}"
    spec = importlib.util.spec_from_file_location(module_name, str(py_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create spec for {module_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
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
