"""tistory hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class TistoryAddon(HostingAddon):
    addon_id = "tistory"
    name = "Tistory"
    category = "portal"
    category_name = "국내 포털 & 블로그"
    domains = ["tistory.com", "kakaocdn.net", "daumcdn.net"]
    badge_class = "badge-tistory"
    badge_label = "Tistory (티스토리 첨부파일)"
    badge_style = {
        "background": "rgba(255, 86, 56, 0.15)",
        "color": "#ff6a4d",
        "border": "1px solid rgba(255, 86, 56, 0.3)",
    }
    description = "티스토리 첨부파일 일괄 추출 및 게시글 제목별 하위 폴더 자동 분류"
    priority = 70

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.tistory import TistoryResolver

        return TistoryResolver()
