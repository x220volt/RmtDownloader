"""transferit hosting addon metadata and task integration."""

from typing import Any, Callable, Dict, Optional

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class TransferitAddon(HostingAddon):
    addon_id = "transferit"
    name = "Transfer.it"
    category = "cloud"
    domains = ["transfer.it"]
    badge_class = "badge-transferit"
    badge_label = "Transfer.it"
    badge_style = {
        "background": "rgba(239, 68, 68, 0.15)",
        "color": "#f87171",
        "border": "1px solid rgba(239, 68, 68, 0.3)",
    }
    description = "MEGA 기반 AES-128-CTR 실시간 스트리밍 복호화 및 이어받기 지원"
    priority = 100

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.transferit import TransferitResolver

        return TransferitResolver()

    def can_restore_task(self, job: Any) -> bool:
        opts = getattr(job, "options", {}) or {}
        return bool(opts.get("node_handle") and opts.get("node_key"))

    def restore_task(
        self,
        job: Any,
        progress_callback: Optional[Callable] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Any:
        from ...resolvers.transferit import TransferitDownloadTask

        opts = getattr(job, "options", {}) or {}
        chunk_kb = (settings or {}).get("chunk_size_kb", 512)
        return TransferitDownloadTask(
            url=getattr(job, "resolved_url", "") or getattr(job, "original_url", ""),
            dest_dir=getattr(job, "download_dir", ""),
            filename=getattr(job, "filename", ""),
            partial_id=getattr(job, "id", ""),
            password=opts.get("password"),
            node_handle=opts.get("node_handle"),
            node_key=opts.get("node_key"),
            node_size=opts.get("node_size"),
            pw_token=opts.get("pw_token"),
            xh=opts.get("xh"),
            chunk_size=chunk_kb * 1024,
            progress_callback=progress_callback,
        )
