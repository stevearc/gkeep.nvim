import importlib.util
import subprocess
import sys
from types import ModuleType
from typing import Any, Optional


class LazyModule:
    _mod: Optional[ModuleType]

    def __init__(self, module_name: str):
        self._module_name = module_name
        self._mod = None

    @property
    def mod(self) -> Any:
        if self._mod is None:
            del sys.modules[self._module_name]
            self._mod = __import__(self._module_name)
        return self._mod

    def __getattr__(self, key: str) -> Any:
        if key == "__spec__":
            return importlib.util.find_spec(self._module_name)
        return getattr(self.mod, key)


# gpsoauth is crazy slow to import. This speeds up load time by quite a bit
sys.modules["gpsoauth"] = LazyModule("gpsoauth")  # type: ignore[assignment]

try:
    import gkeepapi
    import keyring
    import typing_extensions
except ImportError:
    # gpsoauth doesn't work with urllib3 2.0
    modules = ["urllib3<2.0", "gkeepapi", "keyring", "typing-extensions"]
    subprocess.call([sys.executable, "-m", "pip", "install", "-q"] + modules)
    try:
        import gkeepapi
        import keyring
        import typing_extensions
    except ImportError as e:
        raise ImportError(
            "Could not auto-install gkeepapi, keyring, and typing_extensions. Please `pip install gkeepapi keyring typing_extensions` in your neovim python environment"
        ) from e

from gkeep.plugin import GkeepPlugin

__all__ = ["GkeepPlugin"]
