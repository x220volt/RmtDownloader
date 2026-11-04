"""datanodes hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class DataNodesAddon(HostingAddon):
    addon_id = "datanodes"
    name = "DataNodes"
    category = "cloud"
    domains = ["datanodes.to"]
    badge_class = "badge-datanodes"
    badge_label = "DataNodes (datanodes.to)"
    badge_style = {
        "background": "rgba(59, 130, 246, 0.15)",
        "color": "#60a5fa",
        "border": "1px solid rgba(59, 130, 246, 0.3)",
    }
    description = "DataNodes 2단계 인증 및 Turnstile 자동 우회 다운로드 지원"
    priority = 37

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.datanodes import DataNodesResolver

        return DataNodesResolver()
