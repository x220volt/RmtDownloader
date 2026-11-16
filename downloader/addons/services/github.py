"""github hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class GitHubAddon(HostingAddon):
    addon_id = "github"
    name = "GitHub"
    category = "cloud"
    domains = ["github.com", "githubusercontent.com", "codeload.github.com"]
    badge_class = "badge-github"
    badge_label = "GitHub (Releases / Archive)"
    badge_style = {
        "background": "rgba(148, 163, 184, 0.18)",
        "color": "#f8fafc",
        "border": "1px solid rgba(148, 163, 184, 0.35)",
    }
    description = "GitHub 릴리즈(latest/tag) 에셋 자동 추출, 다중 파일 일괄 다운로드 및 저장소 아카이브/Raw 지원"
    priority = 145

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.github import GitHubResolver

        return GitHubResolver()
