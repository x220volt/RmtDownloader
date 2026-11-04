"""torrent hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class TorrentAddon(HostingAddon):
    addon_id = "torrent"
    name = "BitTorrent"
    category = "p2p"
    category_name = "토렌트 & P2P"
    domains = ["magnet:", ".torrent"]
    badge_class = "badge-torrent"
    badge_label = "BitTorrent & Magnet"
    badge_style = {
        "background": "rgba(99, 102, 241, 0.15)",
        "color": "#818cf8",
        "border": "1px solid rgba(99, 102, 241, 0.3)",
    }
    description = "내장 aria2c 엔진을 통한 고속 BitTorrent 및 Magnet 다운로드"
    priority = 10

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.torrent import TorrentResolver

        return TorrentResolver()
