"""mega hosting addon metadata and task integration."""

from typing import Any, Callable, Dict, Optional

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class MegaAddon(HostingAddon):
    addon_id = "mega"
    name = "MEGA"
    category = "cloud"
    domains = ["mega.nz", "mega.co.nz", "mega.io"]
    badge_class = "badge-mega"
    badge_label = "MEGA (mega.nz)"
    badge_style = {
        "background": "rgba(239, 68, 68, 0.15)",
        "color": "#ef4444",
        "border": "1px solid rgba(239, 68, 68, 0.3)",
    }
    description = (
        "MEGA 직링크 발급, 실시간 AES-128-CTR 스트리밍 복호화 및 이어받기 지원"
    )
    priority = 110

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.mega import MegaResolver

        return MegaResolver()

    def can_restore_task(self, job: Any) -> bool:
        opts = getattr(job, "options", {}) or {}
        return bool(opts.get("mega_file_id") and opts.get("mega_key_a32"))

    def restore_task(
        self,
        job: Any,
        progress_callback: Optional[Callable] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Any:
        from ...resolvers.mega import MegaDownloadTask

        opts = getattr(job, "options", {}) or {}
        chunk_kb = (settings or {}).get("chunk_size_kb", 512)
        return MegaDownloadTask(
            url=getattr(job, "resolved_url", "") or getattr(job, "original_url", ""),
            dest_dir=getattr(job, "download_dir", ""),
            filename=getattr(job, "filename", ""),
            partial_id=getattr(job, "id", ""),
            file_id=opts.get("mega_file_id"),
            key_a32=opts.get("mega_key_a32"),
            file_size=opts.get("mega_file_size"),
            chunk_size=chunk_kb * 1024,
            progress_callback=progress_callback,
        )
