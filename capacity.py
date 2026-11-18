"""Offline capacity checks. CPU is in cores, memory in bytes, GPU in devices."""

from fractions import Fraction
from math import isfinite


_KEYS = ("cpu", "memory", "gpu")


def _vector(values):
    result = {}
    for key in _KEYS:
        value = values[key]
        if key == "cpu" and type(value) is float and isfinite(value):
            value = Fraction(str(value))
        if not (type(value) is int or (key == "cpu" and isinstance(value, Fraction))) or value < 0:
            raise ValueError(f"invalid {key} quantity")
        result[key] = value
    return result


def _effective_allocatable(allocatable, capacity, used):
    remaining = max(capacity - used, 0)
    return min(allocatable, remaining)


def available_resources(capacity, allocatable, used, requested):
    """Subtract existing requests from usage-adjusted headroom; never return negatives."""
    capacity, allocatable, used, requested = map(_vector, (capacity, allocatable, used, requested))
    return {
        k: max(_effective_allocatable(allocatable[k], capacity[k], used[k]) - requested[k], 0)
        for k in _KEYS
    }


def _resources_fit_available(available, required):
    return (
        required["cpu"] <= available["cpu"]
        and required["memory"] <= available["memory"]
        and required["gpu"] <= available["gpu"]
    )


def _per_pod_fits_available(available, per_pod):
    return _resources_fit_available(available, per_pod)


def _can_place_pods_on_cluster(buckets, per_pod, count):
    pools = [dict(bucket) for bucket in buckets]

    for _ in range(count):
        pools.sort(
            key=lambda bucket: (bucket["cpu"], bucket["memory"], bucket["gpu"]),
            reverse=True,
        )
        placed = False

        for bucket in pools:
            if not _per_pod_fits_available(bucket, per_pod):
                continue

            bucket["cpu"] -= per_pod["cpu"]
            bucket["memory"] -= per_pod["memory"]
            bucket["gpu"] -= per_pod["gpu"]
            placed = True
            break

        if not placed:
            return False

    return True


def can_place_pods(buckets, per_pod, count):
    """Check identical additional pods against remaining capacity on eligible nodes.

    Each mapping requires cpu (int, finite float or Fraction), memory and gpu
    (integers); all quantities must be nonnegative. No scheduling constraints
    beyond these three resources are evaluated. No inputs are modified.
    """
    if type(count) is not int or count < 0:
        raise ValueError("count must be a nonnegative integer")
    return _can_place_pods_on_cluster(map(_vector, buckets), _vector(per_pod), count)
