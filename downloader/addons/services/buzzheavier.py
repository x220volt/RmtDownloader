"""buzzheavier hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class BuzzheavierAddon(HostingAddon):
    addon_id = "buzzheavier"
    name = "Buzzheavier"
    category = "cloud"
    domains = ["buzzheavier.com", "bzzhr.to"]
    badge_class = "badge-buzzheavier"
    badge_label = "Buzzheavier"
    badge_style = {
        "background": "rgba(245, 158, 11, 0.15)",
        "color": "#fbbf24",
        "border": "1px solid rgba(245, 158, 11, 0.3)",
    }
    description = "Buzzheavier / Bzzhr 고속 클라우드 다운로드 및 스텔스 직링크 발급 지원"
    priority = 36

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.buzzheavier import BuzzheavierResolver

        return BuzzheavierResolver()

