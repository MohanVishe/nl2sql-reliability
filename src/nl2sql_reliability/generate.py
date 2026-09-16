"""Talk to a model and get text back.

One primitive -- `chat(messages)` -- because the agentic arm needs multiple turns and the
single-shot arm is just the one-message case. Every provider in this study speaks that shape.

Written against the standard library only. A study whose results depend on a client library
version is a study that cannot be reproduced two years later, and the HTTP here is four
fields wide.

**Context truncation is treated as an error, not as a short answer.** If a prompt exceeds the
context window, Ollama silently drops the front of it -- which is where the schema lives. The
model would then answer from a partial schema and be scored wrong, and the study would report
that as unreliability. `Reply.truncated` catches it instead.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from .prompt import Prompt

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 300.0

# The largest schema in the dataset is ~1,700 tokens; with instructions, question and a 512
# token answer the worst case is ~2,500. 8192 leaves room for the agentic arm's extra turns
# without spilling out of 8 GB of VRAM on a 7B model at Q4.
DEFAULT_NUM_CTX = 8192
DEFAULT_NUM_PREDICT = 512

# Non-zero by design. pass^k measures run-to-run variation, and a greedy decode would make
# every attempt identical -- the study would then measure nothing at all. 0.2 is the low end
# of what a team would actually deploy for SQL generation.
DEFAULT_TEMPERATURE = 0.2

_RETRYABLE = (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError)


@dataclass(frozen=True)
class Reply:
    """What a model returned, plus what it cost."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.truncated


class Generator(Protocol):
    """Anything that can answer a prompt. Arms of the study differ only in this."""

    name: str

    def chat(self, messages: list[dict[str, str]]) -> Reply: ...

    def complete(self, prompt: Prompt | str) -> Reply: ...


@dataclass
class OllamaGenerator:
    """Local inference through Ollama's HTTP API.

    Local is the primary arm of this study, not the fallback. It has no quota, so k can be
    large enough for pass^k to mean something -- a hosted free tier caps out around k=3 per
    day, and a reliability figure at k=3 is barely a figure.
    """

    model: str = "qwen2.5-coder:7b"
    host: str = DEFAULT_HOST
    temperature: float = DEFAULT_TEMPERATURE
    num_ctx: int = DEFAULT_NUM_CTX
    num_predict: int = DEFAULT_NUM_PREDICT
    timeout: float = DEFAULT_TIMEOUT
    retries: int = 2
    seed: int | None = None
    options: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"

    def complete(self, prompt: Prompt | str) -> Reply:
        return self.chat([{"role": "user", "content": str(prompt)}])

    def chat(self, messages: list[dict[str, str]]) -> Reply:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
                **({"seed": self.seed} if self.seed is not None else {}),
                **self.options,
            },
        }

        started = time.perf_counter()
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                body = self._post("/api/chat", payload)
            # HTTPError subclasses URLError, so it must be caught first or the retry branch
            # below would swallow it and retry a request the server has already rejected.
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                return Reply(
                    text="",
                    error=f"HTTP {exc.code}: {detail}",
                    elapsed_seconds=time.perf_counter() - started,
                )
            except _RETRYABLE as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.retries:
                    # A local server that is loading a model into VRAM refuses connections
                    # for a few seconds. Backing off is cheaper than failing the run.
                    time.sleep(2**attempt)
                    continue
                break

            prompt_tokens = int(body.get("prompt_eval_count", 0))
            return Reply(
                text=body.get("message", {}).get("content", ""),
                prompt_tokens=prompt_tokens,
                completion_tokens=int(body.get("eval_count", 0)),
                elapsed_seconds=time.perf_counter() - started,
                # Ollama truncates from the front without saying so. Equality, not >: the
                # count reported is what survived, so a full window means the rest was cut.
                truncated=prompt_tokens >= self.num_ctx,
            )

        return Reply(text="", error=last_error, elapsed_seconds=time.perf_counter() - started)

    def _post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.host.rstrip('/')}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def available(self) -> bool:
        """Whether the server is up and this model is pulled."""
        try:
            request = urllib.request.Request(f"{self.host.rstrip('/')}/api/tags")
            with urllib.request.urlopen(request, timeout=5) as response:
                tags = json.loads(response.read().decode("utf-8"))
        except Exception:
            return False
        names = {model.get("name", "") for model in tags.get("models", [])}
        return self.model in names or f"{self.model}:latest" in names


@dataclass
class StubGenerator:
    """A scripted generator, for tests and for dry runs of the harness.

    The harness is the thing most likely to be wrong, and debugging it against a real model
    means waiting seconds per call for an answer that was never the point.
    """

    replies: list[str] = field(default_factory=list)
    name: str = "stub"
    calls: list[list[dict[str, str]]] = field(default_factory=list)

    def chat(self, messages: list[dict[str, str]]) -> Reply:
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self.replies) - 1) if self.replies else -1
        text = self.replies[index] if self.replies else ""
        return Reply(text=text, prompt_tokens=len(str(messages)) // 4, completion_tokens=8)

    def complete(self, prompt: Prompt | str) -> Reply:
        return self.chat([{"role": "user", "content": str(prompt)}])
