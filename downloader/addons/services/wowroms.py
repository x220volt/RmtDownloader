"""wowroms hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class WowromsAddon(HostingAddon):
    addon_id = "wowroms"
    name = "WoWroms"
    category = "cloud"
    domains = ["wowroms.com"]
    badge_class = "badge-wowroms"
    badge_label = "WoWroms (wowroms.com)"
    badge_style = {
        "background": "rgba(245, 158, 11, 0.15)",
        "color": "#fbbf24",
        "border": "1px solid rgba(245, 158, 11, 0.3)",
    }
    description = "WoWroms 레트로 게임 ROM, ISO 및 에뮬레이터 다운로드 지원"
    priority = 175

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.wowroms import WowromsResolver

        return WowromsResolver()
