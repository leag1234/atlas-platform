"""Minimal HTTP client for the OpenAI Responses, Files and Batch APIs (urllib, no SDK).

Offline evaluation tooling only: nothing in services/ imports this module.
The API key is read from the environment and never written to any output.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

DEFAULT_BASE_URL = "https://api.openai.com/v1"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class OpenAIHttp:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 300.0,
        retries: int = 3,
        backoff: float = 5.0,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise ApiError(0, "OPENAI_API_KEY is not set")
        self._key = key
        self.base_url = (
            base_url or os.environ.get("FRONTIER_JUDGE_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "[key]")

    def _request(
        self, method: str, path: str, data: bytes | None, content_type: str | None
    ) -> bytes:
        headers = {"Authorization": f"Bearer {self._key}"}
        if content_type:
            headers["Content-Type"] = content_type
        last = ApiError(0, "no attempt")
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                self.base_url + path, data=data, headers=headers, method=method
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body: bytes = resp.read()
                    return body
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:400].decode("utf-8", "replace")
                last = ApiError(exc.code, self._scrub(detail))
                if exc.code not in RETRY_STATUSES:
                    raise last from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = ApiError(0, self._scrub(str(exc)))
            if attempt < self.retries:
                time.sleep(self.backoff * (2**attempt))
        raise last

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raw = self._request(
            "POST", path, json.dumps(payload).encode("utf-8"), "application/json"
        )
        result: dict[str, Any] = json.loads(raw)
        return result

    def get_json(self, path: str) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self._request("GET", path, None, None))
        return result

    def get_bytes(self, path: str) -> bytes:
        return self._request("GET", path, None, None)

    def upload_file(self, filename: str, content: bytes, purpose: str = "batch") -> str:
        boundary = "frontierjudge" + uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\n{purpose}\r\n'.encode(),
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                "Content-Type: application/jsonl\r\n\r\n"
            ).encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        raw = self._request(
            "POST",
            "/files",
            b"".join(parts),
            f"multipart/form-data; boundary={boundary}",
        )
        file_id: str = json.loads(raw)["id"]
        return file_id

    # Responses API
    def create_response(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.post_json("/responses", body)

    # Batch API
    def create_batch(
        self, input_file_id: str, endpoint: str = "/v1/responses"
    ) -> dict[str, Any]:
        return self.post_json(
            "/batches",
            {
                "input_file_id": input_file_id,
                "endpoint": endpoint,
                "completion_window": "24h",
            },
        )

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        return self.get_json(f"/batches/{batch_id}")

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        return self.post_json(f"/batches/{batch_id}/cancel", {})

    def file_content(self, file_id: str) -> bytes:
        return self.get_bytes(f"/files/{file_id}/content")


def output_text(response: dict[str, Any]) -> str:
    """Concatenate the output_text parts of a Responses API object."""
    texts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                texts.append(str(part.get("text", "")))
    return "".join(texts)


def usage_of(response: dict[str, Any]) -> tuple[int, int]:
    usage = response.get("usage") or {}
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
