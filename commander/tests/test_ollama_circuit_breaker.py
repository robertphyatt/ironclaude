"""Unit tests for the URL-keyed Ollama circuit breaker."""
from ironclaude.ollama_client import (
    _CircuitBreakerRegistry, _BREAKER_BASE_BACKOFF, _BREAKER_MAX_BACKOFF,
)


class _Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


def test_closed_url_is_allowed():
    assert _CircuitBreakerRegistry(now=_Clock()).allow("http://a") is True


def test_first_failure_opens_for_base_backoff():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    reg.record_failure("http://a")
    assert reg.allow("http://a") is False
    clk.advance(_BREAKER_BASE_BACKOFF + 0.1)
    assert reg.allow("http://a") is True          # half-open probe


def test_only_one_prober_in_half_open():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    reg.record_failure("http://a")
    clk.advance(_BREAKER_BASE_BACKOFF + 0.1)
    assert reg.allow("http://a") is True           # first caller claims the probe
    assert reg.allow("http://a") is False          # second caller blocked until probe resolves


def test_backoff_doubles_capped():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    reg.record_failure("http://a")                 # backoff 5
    clk.advance(_BREAKER_BASE_BACKOFF + 0.1); reg.allow("http://a"); reg.record_failure("http://a")   # backoff 10
    for _ in range(20):
        clk.advance(_BREAKER_MAX_BACKOFF + 1); reg.allow("http://a"); reg.record_failure("http://a")
    assert reg.backoff_for("http://a") == _BREAKER_MAX_BACKOFF


def test_success_closes_and_resets():
    reg = _CircuitBreakerRegistry(now=_Clock())
    reg.record_failure("http://a"); reg.record_success("http://a")
    assert reg.allow("http://a") is True
    assert reg.backoff_for("http://a") is None      # absent = closed


def test_urls_isolated():
    reg = _CircuitBreakerRegistry(now=_Clock())
    reg.record_failure("http://a")
    assert reg.allow("http://a") is False
    assert reg.allow("http://b") is True


def test_reset_clears_all():
    reg = _CircuitBreakerRegistry(now=_Clock())
    reg.record_failure("http://a")
    reg.reset()
    assert reg.allow("http://a") is True
