"""kio hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class KioAddon(HostingAddon):
    addon_id = "kio"
    name = "kio.ac"
    category = "cloud"
    domains = ["kio.ac"]
    badge_class = "badge-kio"
    badge_label = "kio.ac"
    badge_style = {
        "background": "rgba(20, 184, 166, 0.15)",
        "color": "#2dd4bf",
        "border": "1px solid rgba(20, 184, 166, 0.3)",
    }
    description = "CBOR 컬렉션/디렉터리 API 해석 및 비밀번호 해제"
    priority = 130

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.kio import KioResolver

        return KioResolver()
