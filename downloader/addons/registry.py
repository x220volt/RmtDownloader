"""호스팅 애드온 레지스트리 및 관리자 모듈."""

import logging
from typing import Any, Dict, List, Optional

from ..resolvers.base import BaseResolver
from .base import HostingAddon

logger = logging.getLogger(__name__)


class AddonRegistry:
    """모든 호스팅 및 클라우드 애드온을 등록하고 관리하는 레지스트리."""

    def __init__(self):
        self._addons: Dict[str, HostingAddon] = {}
        self._cached_resolvers: Optional[List[BaseResolver]] = None

    def register(self, addon: HostingAddon) -> None:
        """새로운 애드온을 등록합니다."""
        self._addons[addon.addon_id] = addon
        self._cached_resolvers = None
        logger.debug(f"호스팅 애드온 등록 완료: {addon.name} ({addon.addon_id})")

    def get_addon(self, addon_id: str) -> Optional[HostingAddon]:
        """고유 ID로 애드온을 조회합니다."""
        return self._addons.get(addon_id)

    def get_addons(self) -> List[HostingAddon]:
        """우선순위(priority 오름차순)로 정렬된 전체 애드온 목록을 반환합니다."""
        return sorted(self._addons.values(), key=lambda a: a.priority)

    def get_resolvers(self) -> List[BaseResolver]:
        """등록된 모든 애드온의 리졸버 목록을 우선순위 순서대로 반환합니다."""
        if self._cached_resolvers is None:
            self._cached_resolvers = [
                addon.get_resolver() for addon in self.get_addons()
            ]
        return self._cached_resolvers

    def find_for_url(self, url: str) -> Optional[HostingAddon]:
        """주어진 URL을 처리할 수 있는 애드온을 반환합니다."""
        for addon in self.get_addons():
            try:
                if addon.can_handle(url):
                    return addon
            except Exception:
                continue
        return None

    def find_for_service(self, service_name: str) -> Optional[HostingAddon]:
        """서비스 이름으로 애드온을 검색합니다 (대소문자 무관 및 부분 일치)."""
        if not service_name:
            return None
        lower = service_name.lower().strip()
        # 1. 완전 일치 (name 또는 addon_id)
        for addon in self._addons.values():
            if addon.name.lower() == lower or addon.addon_id.lower() == lower:
                return addon
        # 2. 부분 일치 (특정 예외 방지: mega vs megaup)
        if "megaup" in lower:
            return self._addons.get("megaup")
        if "mega" in lower:
            return self._addons.get("mega")
        if "mybox" in lower:
            return self._addons.get("naver_mybox")

        for addon in self.get_addons():
            if addon.name.lower() in lower or addon.addon_id.lower() in lower:
                return addon
        return None

    def find_task_addon(self, job: Any) -> Optional[HostingAddon]:
        """Restore special transfers through addon capabilities, including old jobs."""
        preferred = self.find_for_service(getattr(job, "service_name", ""))
        if preferred and preferred.can_restore_task(job):
            return preferred
        return next((addon for addon in self.get_addons()
                     if addon is not preferred and addon.can_restore_task(job)), None)

    def get_ui_manifest(self) -> Dict[str, Any]:
        """프론트엔드 UI(설정 모달, 뱃지 렌더링, 스타일 적용 등)를 위한 매니페스트를 반환합니다."""
        addons = self.get_addons()
        categories: Dict[str, Dict[str, Any]] = {
            "cloud": {"name": "☁️ 해외 클라우드 & 파일 호스팅", "addons": []},
            "portal": {"name": "📝 국내 포털 & 블로그", "addons": []},
            "p2p": {"name": "⚡ 토렌트 & P2P", "addons": []},
            "misc": {"name": "🔗 웹페이지 및 직접 링크", "addons": []},
        }

        badge_rules: List[Dict[str, Any]] = []

        for addon in addons:
            ad_dict = addon.to_dict()
            cat = addon.category if addon.category in categories else "misc"
            categories[cat]["addons"].append(ad_dict)

            # 뱃지 매핑 규칙 등록
            rule = {
                "addon_id": addon.addon_id,
                "name": addon.name,
                "badge_class": addon.badge_class,
                "badge_label": addon.badge_label or addon.name,
                "badge_style": addon.badge_style,
                "domains": addon.domains,
            }
            badge_rules.append(rule)

        return {
            "addons": [a.to_dict() for a in addons],
            "categories": categories,
            "badge_rules": badge_rules,
        }


# 전역 애드온 레지스트리 싱글톤 인스턴스
addon_registry = AddonRegistry()
