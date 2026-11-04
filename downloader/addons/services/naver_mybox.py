"""naver_mybox hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class NaverMyBoxAddon(HostingAddon):
    addon_id = "naver_mybox"
    name = "Naver MYBOX"
    category = "portal"
    category_name = "국내 포털 & 블로그"
    domains = ["mybox.naver.com"]
    badge_class = "badge-mybox"
    badge_label = "Naver MYBOX (mybox.naver.com)"
    badge_style = {
        "background": "rgba(3, 199, 90, 0.15)",
        "color": "#00c73c",
        "border": "1px solid rgba(3, 199, 90, 0.3)",
    }
    description = "공유 링크 및 파일 속성 API를 통한 고속 직링크 발급 및 이어받기 지원"
    priority = 50

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.naver_mybox import NaverMyBoxResolver

        return NaverMyBoxResolver()
