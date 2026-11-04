"""vimm hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class VimmAddon(HostingAddon):
    addon_id = "vimm"
    name = "Vimm's Lair"
    category = "cloud"
    domains = ["vimm.net"]
    badge_class = "badge-vimm"
    badge_label = "Vimm's Lair (vimm.net)"
    badge_style = {
        "background": "rgba(168, 85, 247, 0.15)",
        "color": "#c084fc",
        "border": "1px solid rgba(168, 85, 247, 0.3)",
    }
    description = "Vimm's Lair 레트로 게임 ROM 자동 직링크 발급 및 다운로드 지원"
    priority = 179

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.vimm import VimmResolver

        return VimmResolver()
