"""naver_cafe hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class NaverCafeAddon(HostingAddon):
    addon_id = "naver_cafe"
    name = "Naver Cafe"
    category = "portal"
    category_name = "국내 포털 & 블로그"
    domains = ["cafe.naver.com"]
    badge_class = "badge-naver"
    badge_label = "Naver Cafe (네이버 카페 첨부파일)"
    badge_style = {
        "background": "rgba(3, 199, 90, 0.15)",
        "color": "#03c75a",
        "border": "1px solid rgba(3, 199, 90, 0.3)",
    }
    description = "게시글 본문 내 모든 첨부파일 일괄 추출 및 게시글 제목별 자동 분류"
    priority = 60

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.naver_cafe import NaverCafeResolver

        return NaverCafeResolver()
