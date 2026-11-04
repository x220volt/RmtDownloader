"""planetemu hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class PlanetEmuAddon(HostingAddon):
    addon_id = "planetemu"
    name = "PlanetEmu"
    category = "cloud"
    domains = ["planetemu.net"]
    badge_class = "badge-planetemu"
    badge_label = "PlanetEmu (planetemu.net)"
    badge_style = {
        "background": "rgba(59, 130, 246, 0.15)",
        "color": "#60a5fa",
        "border": "1px solid rgba(59, 130, 246, 0.3)",
    }
    description = (
        "PlanetEmu 레트로 게임 ROM 자동 쿼터 인증 및 고속 직링크 다운로드 지원"
    )
    priority = 178

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.planetemu import PlanetEmuResolver

        return PlanetEmuResolver()
