from typing import Protocol
from ..models import Coordinates, RouteResult


class Router(Protocol):
    def route(self, origin: Coordinates, destination: Coordinates, *, snap_limits_m=None) -> RouteResult: ...
