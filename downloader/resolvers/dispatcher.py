"""리졸버들을 총괄 관리하고 URL을 해석하는 디스패처 모듈."""

import logging
from typing import List, Optional

from ..url_helper import clean_url
from .base import BaseResolver

logger = logging.getLogger(__name__)


class ResolverDispatcher:
    """등록된 호스팅 애드온 및 리졸버들을 순차적으로 검사하여 적절한 리졸버를 실행합니다."""

    def __init__(self, resolvers: Optional[List[BaseResolver]] = None):
        if resolvers is not None:
            self.resolvers = resolvers
        else:
            from ..addons import addon_registry

            self.resolvers = list(addon_registry.get_resolvers())

    def get_resolver(self, url: str) -> Optional[BaseResolver]:
        """주어진 URL을 처리할 수 있는 리졸버를 반환합니다."""
        for resolver in self.resolvers:
            if resolver.can_handle(url):
                return resolver
        # 단축 URL인 경우 전개하여 대상 클라우드 리졸버 매칭 시도
        from ..url_helper import is_shortened_url, unshorten_url

        if is_shortened_url(url):
            try:
                expanded = unshorten_url(url)
                if expanded and expanded != url:
                    for resolver in self.resolvers:
                        if resolver.can_handle(expanded):
                            return resolver
            except Exception as e:
                logger.debug("단축 URL 전개 매칭 실패 (%s): %s", url, e)
        return None

    def resolve(self, url: str, options: dict | None = None) -> dict:
        """
        URL에 맞는 리졸버를 찾아 해석을 수행합니다.
        적절한 리졸버가 없으면 ValueError를 발생시킵니다.
        """
        cleaned_url = clean_url(url)
        resolver = self.get_resolver(cleaned_url) or self.get_resolver(url)
        if not resolver:
            raise ValueError(f"지원하지 않는 다운로드 링크 형태입니다: {url}")
        target_url = cleaned_url if resolver.can_handle(cleaned_url) else url
        logger.info(f"[{resolver.name}] 리졸버를 사용하여 링크 분석 시작: {target_url}")
        result = resolver.resolve(target_url, options=options)
        logger.info(
            f"[{resolver.name}] 링크 분석 완료: {result.get('filename') or result.get('url')}"
        )
        return result


# 전역 기본 디스패처 인스턴스
default_dispatcher = ResolverDispatcher()
