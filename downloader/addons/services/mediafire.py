"""mediafire hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class MediaFireAddon(HostingAddon):
    addon_id = "mediafire"
    name = "MediaFire"
    category = "cloud"
    domains = ["mediafire.com"]
    badge_class = "badge-mediafire"
    badge_label = "MediaFire (mediafire.com)"
    badge_style = {
        "background": "rgba(0, 114, 255, 0.15)",
        "color": "#38bdf8",
        "border": "1px solid rgba(0, 114, 255, 0.3)",
    }
    description = "MediaFire 직링크 자동 추출 및 고속 분할 다운로드 지원"
    priority = 35

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.mediafire import MediaFireResolver

        return MediaFireResolver()
