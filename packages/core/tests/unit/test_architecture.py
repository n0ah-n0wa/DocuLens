"""Enforces the dependency direction required by SPECIFICATIONS.md §72.

The domain and application layers must not import web frameworks, cloud SDKs, vector stores,
LangChain or persistence libraries, and inner layers must never import outer ones. The check is a
static AST scan so that it also catches imports inside functions and ``TYPE_CHECKING`` blocks.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "doculens"

EXTERNAL_FORBIDDEN = frozenset(
    {
        "aiobotocore",
        "alembic",
        "asyncpg",
        "boto3",
        "botocore",
        "chromadb",
        "fastapi",
        "fitz",
        "httpx",
        "langchain",
        "langchain_community",
        "langchain_core",
        "psycopg",
        "pydantic_settings",
        "pymupdf",
        "redis",
        "sqlalchemy",
        "starlette",
    }
)

LAYER_RULES: dict[str, frozenset[str]] = {
    "domain": EXTERNAL_FORBIDDEN | {"doculens.application", "doculens.infrastructure"},
    "application": EXTERNAL_FORBIDDEN | {"doculens.infrastructure"},
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(node.module)
    return modules


def _is_forbidden(module: str, forbidden: frozenset[str]) -> bool:
    return any(module == name or module.startswith(f"{name}.") for name in forbidden)


@pytest.mark.parametrize("layer", sorted(LAYER_RULES))
def test_layer_has_no_forbidden_imports(layer: str) -> None:
    layer_dir = PACKAGE_ROOT / layer
    assert layer_dir.is_dir(), f"missing layer package: {layer_dir}"

    violations = sorted(
        f"{path.relative_to(PACKAGE_ROOT).as_posix()} imports {module}"
        for path in layer_dir.rglob("*.py")
        for module in _imported_modules(path)
        if _is_forbidden(module, LAYER_RULES[layer])
    )

    assert violations == [], "\n".join(violations)
