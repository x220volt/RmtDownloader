"""TeeTeeTee hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class TeeTeeTeeAddon(HostingAddon):
    addon_id = "teeteetee"
    name = "TeeTeeTee"
    category = "patch"
    domains = ["teeteetee-korean.com"]
    badge_class = "badge-teeteetee"
    badge_label = "TeeTeeTee (한글패치)"
    badge_style = {
        "background": "rgba(234, 88, 12, 0.15)",
        "color": "#fb923c",
        "border": "1px solid rgba(234, 88, 12, 0.3)",
    }
    description = "TeeTeeTee 한글패치 아카이브 프로젝트 페이지에서 최신 xdelta 패치 파일 자동 추출 및 다운로드 지원"
    priority = 175

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.teeteetee import TeeTeeTeeResolver

        return TeeTeeTeeResolver()
