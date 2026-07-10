"""Application error types."""


class DependencyError(Exception):
    """Raised when a call to an external dependency fails.

    See CONTRIBUTING.md — external-dependency failures must be caught at the
    call site, logged as a structured `dependency_failure` event, and
    re-raised as this type with `raise ... from exc`.
    """


class NotFoundError(Exception):
    """Raised by a service when a requested resource does not exist.

    Routers map this to a 404 response. Keeping it distinct from
    ``DependencyError`` lets a thin router translate a missing resource to
    the right status without inspecting SQL.
    """


class InvalidReviewError(Exception):
    """Raised when a review request is semantically invalid.

    Well-formed JSON that still breaks a business rule — e.g. an ``override``
    with no ``catalog_id``, an ``override`` targeting a catalog id that is not
    one of the record's candidates, or an ``accept`` of a record with no
    candidates. Routers map this to a 400 response.
    """
