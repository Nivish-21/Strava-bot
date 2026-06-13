import json
from collections.abc import AsyncIterator

import aiohttp


class OllamaClient:
    def __init__(
        self, base_url: str = "http://localhost:11434", model: str = "gemma3:4b"
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "OllamaClient":
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self.session:
            await self.session.close()

    def _payload(
        self,
        prompt: str,
        system: str | None,
        temperature: float,
        stream: bool,
        num_predict: int | None,
    ) -> dict:
        options: dict = {"temperature": temperature, "num_ctx": 4096}
        if num_predict is not None:
            options["num_predict"] = num_predict
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
            "keep_alive": "5m",
            "options": options,
        }
        if system:
            payload["system"] = system
        return payload

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        num_predict: int | None = None,
    ) -> str:
        assert self.session is not None, "Use as async context manager"
        payload = self._payload(prompt, system, temperature, False, num_predict)
        async with self.session.post(
            f"{self.base_url}/api/generate", json=payload
        ) as resp:
            data = await resp.json()
            return data.get("response", "Error: no response from model")

    async def generate_stream(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        num_predict: int | None = None,
    ) -> AsyncIterator[str]:
        assert self.session is not None, "Use as async context manager"
        payload = self._payload(prompt, system, temperature, True, num_predict)
        async with self.session.post(
            f"{self.base_url}/api/generate", json=payload
        ) as resp:
            async for raw in resp.content:
                line = raw.strip()
                if not line:
                    continue
                chunk = json.loads(line)
                token = chunk.get("response", "")
                if token:
                    yield token
                if chunk.get("done"):
                    break
