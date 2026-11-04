"""onefichier hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class OneFichierAddon(HostingAddon):
    addon_id = "onefichier"
    name = "1fichier"
    category = "cloud"
    domains = ["1fichier.com"]
    badge_class = "badge-onefichier"
    badge_label = "1fichier"
    badge_style = {
        "background": "rgba(245, 158, 11, 0.15)",
        "color": "#fbbf24",
        "border": "1px solid rgba(245, 158, 11, 0.3)",
    }
    description = "무료 다운로드 대기 파라미터 자동 처리 및 직링크 추출"
    priority = 120

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.onefichier import OneFichierResolver

        return OneFichierResolver()
