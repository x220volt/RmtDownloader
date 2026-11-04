"""webpage hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class WebPageAddon(HostingAddon):
    addon_id = "webpage"
    name = "WebPage"
    category = "misc"
    category_name = "웹페이지 및 직접 링크"
    domains = []
    badge_class = "badge-webpage"
    badge_label = "웹페이지 링크 분석"
    badge_style = {
        "background": "rgba(100, 116, 139, 0.15)",
        "color": "#94a3b8",
        "border": "1px solid rgba(100, 116, 139, 0.3)",
    }
    description = "일반 웹페이지/게시글 내 포함된 다운로드 링크 자동 탐색 및 재큐잉"
    priority = 900

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.webpage import WebPageResolver

        return WebPageResolver()
