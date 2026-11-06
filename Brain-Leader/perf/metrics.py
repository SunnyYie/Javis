import math


def summarize(samples, elapsed):
    """Count failures in throughput; compute exact nearest-rank successful latencies."""
    if elapsed <= 0:
        raise ValueError("elapsed must be positive")
    successes = sorted(ms for ms, failed in samples if not failed)

    def percentile(p):
        return successes[max(0, math.ceil(len(successes) * p) - 1)] if successes else None

    return {
        "requests": len(samples), "successes": len(successes),
        "failures": len(samples) - len(successes), "elapsed_seconds": elapsed,
        "completed_qps": len(samples) / elapsed,
        "successful_qps": len(successes) / elapsed,
        "p50_ms": percentile(.50), "p95_ms": percentile(.95), "p99_ms": percentile(.99),
        "percentile_method": "nearest-rank, successful requests, includes owner-thread queue",
        "p99_low_sample_warning": len(successes) < 1000,
        "load_model": "closed-loop, zero think time; elapsed includes graceful drain",
    }
