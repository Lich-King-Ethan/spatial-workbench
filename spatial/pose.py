"""Canonical w,x,y,z quaternions; adapters must verify their coordinate mapping."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Quaternion:
    w: float
    x: float
    y: float
    z: float

    @classmethod
    def parse(cls, values):
        if not isinstance(values, (list, tuple)) or len(values) != 4:
            raise ValueError("orientation must contain w,x,y,z")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("orientation must contain finite numbers")
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm < 1e-9:
            raise ValueError("orientation has no usable norm")
        return cls(*(v / norm for v in values))

    def values(self):
        return (self.w, self.x, self.y, self.z)

    def inverse(self):
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def __mul__(self, other):
        a, b, c, d = self.values()
        e, f, g, h = other.values()
        return Quaternion.parse((a*e-b*f-c*g-d*h, a*f+b*e+c*h-d*g,
                                 a*g-b*h+c*e+d*f, a*h+b*g-c*f+d*e))


IDENTITY = Quaternion(1, 0, 0, 0)
