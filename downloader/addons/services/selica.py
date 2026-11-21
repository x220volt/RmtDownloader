"""셀리카 공방 (selica.kr) 호스팅 애드온 메타데이터 및 연동 모듈."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class SelicaAddon(HostingAddon):
    addon_id = "selica"
    name = "Selica"
    category = "portal"
    domains = ["selica.kr"]
    badge_class = "badge-selica"
    badge_label = "Selica"
    badge_style = {
        "background": "rgba(99, 102, 241, 0.18)",
        "color": "#818cf8",
        "border": "1px solid rgba(99, 102, 241, 0.35)",
    }
    description = "Selica (selica.kr) 패치 및 도구 다운로드 지원"
    priority = 125

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.selica import SelicaResolver

        return SelicaResolver()
