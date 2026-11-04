"""direct hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class DirectAddon(HostingAddon):
    addon_id = "direct"
    name = "Direct"
    category = "direct"
    category_name = "웹페이지 및 직접 링크"
    domains = []
    badge_class = "badge-direct"
    badge_label = "Direct Link (직접 다운로드)"
    badge_style = {
        "background": "rgba(148, 163, 184, 0.15)",
        "color": "#cbd5e1",
        "border": "1px solid rgba(148, 163, 184, 0.3)",
    }
    description = "표준 HTTP/HTTPS 파일 직접 다운로드 (멀티스레드 청크 스트리밍)"
    priority = 999

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.direct import DirectResolver

        return DirectResolver()
