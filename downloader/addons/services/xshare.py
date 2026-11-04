"""xshare hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class XShareAddon(HostingAddon):
    addon_id = "xshare"
    name = "X-Share"
    category = "cloud"
    domains = ["x-share.net"]
    badge_class = "badge-xshare"
    badge_label = "X-Share (x-share.net)"
    badge_style = {
        "background": "rgba(168, 85, 247, 0.15)",
        "color": "#c084fc",
        "border": "1px solid rgba(168, 85, 247, 0.3)",
    }
    description = "단일 파일 및 묶음(Bundle) API 토큰 발급 및 일괄 다운로드"
    priority = 160

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.xshare import XShareResolver

        return XShareResolver()
