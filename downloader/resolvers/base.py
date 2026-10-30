"""리졸버 기본 인터페이스."""

from abc import ABC, abstractmethod


class BaseResolver(ABC):
    """URL을 다운로드 가능한 직링크(및 헤더, 쿠키 등)로 해석하는 베이스 클래스."""

    name: str = "Base"

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """해당 URL을 이 리졸버가 처리할 수 있는지 여부를 반환합니다."""
        pass

    @abstractmethod
    def resolve(self, url: str, options: dict | None = None) -> dict:
        """
        URL을 다운로드 가능한 직링크 정보 딕셔너리로 변환합니다.
        반환 형식:
        {
            "url": "최종 직링크 URL",
            "filename": "추출된 파일명 (선택사항)",
            "headers": {"User-Agent": "..."},
            "cookies": "쿠키 문자열 (선택사항)",
            "referer": "리퍼러 (선택사항)",
            "source_service": "GoFile / VikingFile / Direct 등"
        }
        """
        pass
