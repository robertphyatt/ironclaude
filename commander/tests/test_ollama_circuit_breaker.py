"""Unit tests for the URL-keyed Ollama circuit breaker."""
from ironclaude.ollama_client import (
    _CircuitBreakerRegistry, _BREAKER_BASE_BACKOFF, _BREAKER_MAX_BACKOFF,
    _BREAKER_FAILURE_THRESHOLD, _BREAKER_DEGRADED_GRACE,
)


class _Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


def _trip(reg, url):
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        reg.record_failure(url)


def test_closed_url_is_allowed():
    assert _CircuitBreakerRegistry(now=_Clock()).allow("http://a") is True


def test_threshold_failures_open_for_base_backoff():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    _trip(reg, "http://a")
    assert reg.allow("http://a") is False
    clk.advance(_BREAKER_BASE_BACKOFF + 0.1)
    assert reg.allow("http://a") is True          # half-open probe


def test_only_one_prober_in_half_open():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    _trip(reg, "http://a")
    clk.advance(_BREAKER_BASE_BACKOFF + 0.1)
    assert reg.allow("http://a") is True           # first caller claims the probe
    assert reg.allow("http://a") is False          # second caller blocked until probe resolves


def test_backoff_doubles_capped():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    _trip(reg, "http://a")                         # backoff 5
    clk.advance(_BREAKER_BASE_BACKOFF + 0.1); reg.allow("http://a"); reg.record_failure("http://a")   # backoff 10
    for _ in range(20):
        clk.advance(_BREAKER_MAX_BACKOFF + 1); reg.allow("http://a"); reg.record_failure("http://a")
    assert reg.backoff_for("http://a") == _BREAKER_MAX_BACKOFF


def test_success_closes_and_resets():
    reg = _CircuitBreakerRegistry(now=_Clock())
    _trip(reg, "http://a"); reg.record_success("http://a")
    assert reg.allow("http://a") is True
    assert reg.backoff_for("http://a") is None      # absent = closed


def test_urls_isolated():
    reg = _CircuitBreakerRegistry(now=_Clock())
    _trip(reg, "http://a")
    assert reg.allow("http://a") is False
    assert reg.allow("http://b") is True


def test_reset_clears_all():
    reg = _CircuitBreakerRegistry(now=_Clock())
    _trip(reg, "http://a")
    reg.reset()
    assert reg.allow("http://a") is True


def test_below_threshold_stays_closed():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    for _ in range(_BREAKER_FAILURE_THRESHOLD - 1):
        reg.record_failure("http://a")
    assert reg.allow("http://a") is True
    assert reg.backoff_for("http://a") is None
    assert reg.allow("http://a") is True             # no probe slot claimed below threshold
    clk.advance(_BREAKER_DEGRADED_GRACE + 0.1)
    assert reg.degraded_urls() == []                 # never opened -> not degraded


def test_degraded_urls_respects_grace():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    _trip(reg, "http://a")                                  # opens; open_until=t+5, open_since=t
    assert reg.degraded_urls() == []                        # within grace
    while clk.t - reg._breakers["http://a"].open_since < _BREAKER_DEGRADED_GRACE:
        clk.advance(reg.backoff_for("http://a") + 0.1)      # past current open window
        reg.allow("http://a")                               # claim the half-open probe
        reg.record_failure("http://a")                      # probe fails -> re-opens, backoff grows
    assert reg.degraded_urls() == ["http://a"]              # still actively failing >= grace
    reg.record_success("http://a")
    assert reg.degraded_urls() == []                        # cleared on success

def test_degraded_urls_blip_then_idle_not_degraded():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    _trip(reg, "http://a")                                  # momentary blip opens for base backoff only
    clk.advance(_BREAKER_DEGRADED_GRACE + 1)                # no further traffic/probes
    assert reg.degraded_urls() == []                        # window long expired; not still failing


def test_degraded_urls_ignores_sub_threshold():
    clk = _Clock(); reg = _CircuitBreakerRegistry(now=clk)
    for _ in range(_BREAKER_FAILURE_THRESHOLD - 1):
        reg.record_failure("http://a")
    clk.advance(_BREAKER_DEGRADED_GRACE + 1)
    assert reg.degraded_urls() == []
