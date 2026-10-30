"""호스팅 및 클라우드 서비스 애드온 패키지."""

from .base import HostingAddon
from .definitions import register_default_addons
from .registry import AddonRegistry, addon_registry

# 기본 애드온 자동 등록
register_default_addons(addon_registry)

__all__ = ["HostingAddon", "AddonRegistry", "addon_registry"]
