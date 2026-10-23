"""Owner-configured execution limits; long conversations have no default cutoff."""
import math
import time

from .core import Invalid


def seconds(config):
    value = config.get('turn_timeout_seconds', 0)
    if (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
        raise Invalid('turn_timeout_seconds must be a finite nonnegative number; 0 disables the execution cutoff')
    return value


def deadline(config):
    limit = seconds(config)
    return time.monotonic() + limit if limit else math.inf
