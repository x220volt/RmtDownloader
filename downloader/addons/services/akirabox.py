"""AkiraBox hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class AkiraBoxAddon(HostingAddon):
    addon_id = "akirabox"
    name = "AkiraBox"
    category = "cloud"
    domains = ["akirabox.to", "akirabox.com", "akirabox.net"]
    badge_class = "badge-akirabox"
    badge_label = "AkiraBox"
    badge_style = {
        "background": "rgba(225, 29, 72, 0.15)",
        "color": "#fb7185",
        "border": "1px solid rgba(225, 29, 72, 0.3)",
    }
    description = "AkiraBox 고속 다운로드 및 Cloudflare Turnstile 연동 지원"
    priority = 35

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.akirabox import AkiraBoxResolver

        return AkiraBoxResolver()
