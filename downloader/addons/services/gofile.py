"""gofile hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class GofileAddon(HostingAddon):
    addon_id = "gofile"
    name = "GoFile"
    category = "cloud"
    domains = ["gofile.io"]
    badge_class = "badge-gofile"
    badge_label = "GoFile"
    badge_style = {
        "background": "rgba(14, 165, 233, 0.15)",
        "color": "#38bdf8",
        "border": "1px solid rgba(14, 165, 233, 0.3)",
    }
    description = "QJSEngine 기반 웹 토큰(wt) 발급 및 비밀번호 자동 풀이 지원"
    priority = 20

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.gofile import GofileResolver

        return GofileResolver()
