"""Platform abstraction interface."""
from abc import ABC, abstractmethod


class BasePlatform(ABC):
    @abstractmethod
    def set_auto_start(self, enabled: bool) -> None:
        """Enable or disable auto-start on system login."""

    @abstractmethod
    def check_single_instance(self) -> bool:
        """Return True if we are the first instance."""

    @abstractmethod
    def get_app_path(self) -> str:
        """Return the executable or script path for auto-start."""
