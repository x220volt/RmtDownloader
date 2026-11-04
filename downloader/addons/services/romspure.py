"""romspure hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class RomspureAddon(HostingAddon):
    addon_id = "romspure"
    name = "Romspure"
    category = "cloud"
    domains = ["romspure.cc"]
    badge_class = "badge-romspure"
    badge_label = "Romspure (romspure.cc)"
    badge_style = {
        "background": "rgba(244, 63, 94, 0.15)",
        "color": "#fb7185",
        "border": "1px solid rgba(244, 63, 94, 0.3)",
    }
    description = "Romspure 레트로 게임 ROM 자동 직링크 발급 및 Cloudflare 우회 지원"
    priority = 172

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.romspure import RomspureResolver

        return RomspureResolver()
