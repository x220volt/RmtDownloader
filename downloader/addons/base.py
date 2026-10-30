"""호스팅 및 클라우드 서비스 애드온(Addon) 기본 인터페이스."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from ..resolvers.base import BaseResolver


class HostingAddon(ABC):
    """
    각 호스팅/클라우드 서비스를 모듈화하여 캡슐화하는 애드온 기본 클래스.
    - 메타데이터 (이름, 뱃지 스타일, 카테고리, 도메인 등)
    - 리졸버 (URL -> 직링크 및 메타데이터 추출)
    - 다운로드 태스크 팩토리 (특수 스트리밍/복호화가 필요한 경우)
    """

    addon_id: str = "base"
    name: str = "Base Addon"
    category: str = "cloud"  # 'cloud' | 'portal' | 'p2p' | 'direct' | 'misc'
    category_name: str = "해외 클라우드 & 파일 호스팅"
    domains: List[str] = []
    badge_class: str = "badge-direct"
    badge_label: str = ""
    badge_style: Dict[str, str] = {}
    description: str = ""
    priority: int = 100  # 디스패치 우선순위 (낮을수록 먼저 매칭)

    def __init__(self):
        self._resolver_instance: Optional[BaseResolver] = None

    @abstractmethod
    def create_resolver(self) -> BaseResolver:
        """애드온의 리졸버 인스턴스를 생성하여 반환합니다."""
        pass

    def get_resolver(self) -> BaseResolver:
        """캐시된 리졸버 인스턴스를 반환합니다."""
        if self._resolver_instance is None:
            self._resolver_instance = self.create_resolver()
        return self._resolver_instance

    def can_handle(self, url: str) -> bool:
        """주어진 URL을 이 애드온이 처리할 수 있는지 여부를 확인합니다."""
        return self.get_resolver().can_handle(url)

    def can_restore_task(self, job: Any) -> bool:
        """작업 재시도(Retry) 또는 복원 시 이 애드온의 특수 다운로드 태스크를 복원할 수 있는지 확인합니다."""
        return False

    def restore_task(
        self,
        job: Any,
        progress_callback: Optional[Callable] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """작업 재시도 시 특수 다운로드 태스크 인스턴스를 생성합니다."""
        return None

    def to_dict(self) -> Dict[str, Any]:
        """프론트엔드 UI를 위한 애드온 메타데이터 딕셔너리를 반환합니다."""
        return {
            "addon_id": self.addon_id,
            "name": self.name,
            "category": self.category,
            "category_name": self.category_name,
            "domains": self.domains,
            "badge_class": self.badge_class,
            "badge_label": self.badge_label or self.name,
            "badge_style": self.badge_style,
            "description": self.description,
            "priority": self.priority,
        }
