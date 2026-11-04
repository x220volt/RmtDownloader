"""terabox hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class TeraBoxAddon(HostingAddon):
    addon_id = "terabox"
    name = "TeraBox"
    category = "cloud"
    domains = ["1024terabox.com", "terabox.app", "terabox.com", "freeterabox.com"]
    badge_class = "badge-terabox"
    badge_label = "TeraBox"
    badge_style = {
        "background": "rgba(59, 130, 246, 0.15)",
        "color": "#60a5fa",
        "border": "1px solid rgba(59, 130, 246, 0.3)",
    }
    description = "직접 다운로드 및 내 계정 즉시 담기(save 모드), 공유 암호 자동 풀이"
    priority = 80

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.terabox import TeraBoxResolver

        return TeraBoxResolver()
