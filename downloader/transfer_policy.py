"""Shared HTTP engine selection and request options, independent of queue management."""

from dataclasses import dataclass


@dataclass
class HttpTransferPolicy:
    request_kwargs: dict
    method: str
    post_data: object
    chunk_size: int
    split: int
    use_aria2: bool

    @classmethod
    def from_job(cls, job, resolved, settings):
        def option(key, default=None):
            return resolved[key] if key in resolved else job.options.get(key, default)

        method = (option("method", "GET") or "GET").upper()
        post_data = option("post_data")
        # Resolvers may prohibit range requests or limit connections for a host.
        split = max(1, min(16, int(settings.get("aria2_http_split", 8))))
        limit = option("max_connections")
        if limit is not None:
            split = min(split, max(1, int(limit)))
        request = dict(
            url=job.resolved_url,
            dest_dir=job.download_dir,
            filename=job.filename,
            partial_id=job.id,
            headers=option("headers"),
            cookies=option("cookies"),
            referer=option("referer"),
        )
        use_aria2 = bool(
            settings.get("aria2_http_enabled", False)
            and method == "GET"
            and not post_data
            and option("allow_parallel", True)
            and (job.resolved_url or "").lower().startswith(("http://", "https://"))
        )
        return cls(
            request,
            method,
            post_data,
            settings.get("chunk_size_kb", 256) * 1024,
            split,
            use_aria2,
        )

    @property
    def standard_kwargs(self):
        return dict(
            self.request_kwargs,
            method=self.method,
            post_data=self.post_data,
            chunk_size=self.chunk_size,
        )
