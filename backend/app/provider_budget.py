"""Per-command allowance shared across async fan-out and worker threads."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class CallBudget:
    limit: int
    used: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def consume(self, provider: str):
        from .providers.base import ProviderError
        with self._lock:
            if self.used >= self.limit:
                raise ProviderError("The command's external-call allowance is exhausted; no further request was sent.",
                    provider=provider, code="command_budget_exhausted")
            self.used += 1


_current = ContextVar("platform_provider_budget", default=None)


def consume_call(provider: str):
    budget = _current.get()
    if budget is not None:
        budget.consume(provider)


@contextmanager
def limit_calls(limit: int):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("Call allowance must be a nonnegative integer")
    budget = CallBudget(limit)
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)
