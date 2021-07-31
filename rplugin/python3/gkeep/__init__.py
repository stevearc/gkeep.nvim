import subprocess
import sys

try:
    import gkeepapi
    import keyring

    if sys.version_info < (3, 8):
        import typing_extensions
except ImportError:
    modules = ["gkeepapi", "keyring"]
    if sys.version_info < (3, 8):
        modules.apend("typing-extensions")
    subprocess.call([sys.executable, "-m", "pip", "install", "-q"] + modules)
    try:
        import gkeepapi
        import keyring

        if sys.version_info < (3, 8):
            import typing_extensions
    except ImportError as e:
        raise ImportError(
            "Could not auto-install gkeepapi and keyring. Please `pip install gkeepapi keyring` in your neovim python environment"
        ) from e

from gkeep.plugin import GkeepPlugin

__all__ = ["GkeepPlugin"]
