"""Media system factory.

Provides factory-pattern creation of media simulation backends based on
the configured MediaSystemBackend type.
"""

from typing import Dict, Type

from .media_backend import MediaSystemBackend
from .media_config import MediaConfig
from .base_media import BaseMediaSystem


class MediaSystemFactory:
    """Factory for creating media simulation backend instances.

    Usage:
        config = MediaConfig(media_type=MediaSystemBackend.ANALYTIC, ...)
        media_system = MediaSystemFactory.create(config)
    """

    _backends: Dict[MediaSystemBackend, Type[BaseMediaSystem]] = {}

    @classmethod
    def register(cls, backend: MediaSystemBackend, system_cls: Type[BaseMediaSystem]):
        """Register a backend class for the given backend type.

        Args:
            backend: The MediaSystemBackend enum value.
            system_cls: The BaseMediaSystem subclass to associate.
        """
        cls._backends[backend] = system_cls

    @classmethod
    def create(cls, config: MediaConfig) -> BaseMediaSystem:
        """Create a media system instance based on the config's media_type.

        Args:
            config: MediaConfig specifying which backend to create.

        Returns:
            An instance of the appropriate BaseMediaSystem subclass.

        Raises:
            ValueError: If the backend type is not registered.
        """
        backend = config.media_type
        if backend not in cls._backends:
            # Try to auto-import backends on first use
            cls._auto_register()
        if backend not in cls._backends:
            raise ValueError(
                f"Unknown media backend: {backend}. "
                f"Available backends: {list(cls._backends.keys())}"
            )
        simulator_class = cls._backends[backend]
        return simulator_class(config)

    @classmethod
    def _auto_register(cls):
        """Lazily import and register all built-in backend implementations."""
        try:
            from .analytic_media_system import AnalyticMediaSystem
            cls._backends.setdefault(
                MediaSystemBackend.ANALYTIC, AnalyticMediaSystem
            )
        except ImportError:
            pass

        try:
            from .ramulator_media_system import RamulatorMediaSystem
            cls._backends.setdefault(
                MediaSystemBackend.RAMULATOR, RamulatorMediaSystem
            )
        except ImportError:
            pass

        try:
            from .mqsim_media_system import MQSimMediaSystem
            cls._backends.setdefault(
                MediaSystemBackend.MQSIM, MQSimMediaSystem
            )
        except ImportError:
            pass
