"""Hangulteo hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class HangulteoAddon(HostingAddon):
    addon_id = "hangulteo"
    name = "Hangulteo"
    category = "patch"
    domains = ["hangulteo.com"]
    badge_class = "badge-hangulteo"
    badge_label = "한글터 (한글패치)"
    badge_style = {
        "background": "rgba(16, 185, 129, 0.15)",
        "color": "#34d399",
        "border": "1px solid rgba(16, 185, 129, 0.3)",
    }
    description = "한글터 (hangulteo.com) 한글패치 아카이브에서 PC 및 모바일 패치 자동 추출 및 다운로드 지원"
    priority = 176

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.hangulteo import HangulteoResolver

        return HangulteoResolver()
