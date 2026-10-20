class EstimatorError(Exception):
    """利用者へ説明できる見積エラー。"""


class ValidationError(EstimatorError):
    pass


class ConfigError(EstimatorError):
    pass


class CustomerDataError(EstimatorError):
    pass


class GeocodingUnavailableError(EstimatorError):
    pass


class GeocodingNotPreciseError(EstimatorError):
    pass


class CacheError(EstimatorError):
    pass


class RoutingUnavailableError(EstimatorError):
    pass


class NoRouteError(EstimatorError):
    pass


class SnapDistanceError(EstimatorError):
    pass
