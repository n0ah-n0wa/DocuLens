import pytest

from doculens.domain.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)

pytestmark = pytest.mark.unit


class DocumentNotFoundError(NotFoundError):
    code = "DOCUMENT_NOT_FOUND"
    default_message = "The document was not found."


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (DomainError, "DOMAIN_ERROR"),
        (InvalidInputError, "INVALID_INPUT"),
        (NotFoundError, "NOT_FOUND"),
        (ConflictError, "CONFLICT"),
        (PermissionDeniedError, "PERMISSION_DENIED"),
    ],
)
def test_each_category_has_a_stable_code_and_safe_default_message(
    error_type: type[DomainError], code: str
) -> None:
    error = error_type()

    assert error.code == code
    assert error.message == error_type.default_message
    assert str(error) == error.message


def test_a_custom_message_overrides_the_default() -> None:
    error = NotFoundError("Collection 42 is not visible to you.")

    assert error.message == "Collection 42 is not visible to you."


def test_feature_errors_specialise_the_code_but_keep_the_category() -> None:
    error = DocumentNotFoundError()

    assert isinstance(error, NotFoundError)
    assert error.code == "DOCUMENT_NOT_FOUND"
    assert error.message == "The document was not found."
