from __future__ import annotations

from supportability_gate.architecture_policy import _layer, evaluate_architecture
from supportability_gate.contract import GateAdapter, parse_contract


def _policy(language: str = "python"):
    complexity = (
        "python.c901-touched.v1"
        if language == "python"
        else "typescript.c901-equivalent-touched.v1"
    )
    architecture = (
        "python.import-linter.v1" if language == "python" else "typescript.import-boundaries.v1"
    )
    return parse_contract(
        f'''schema_version = "1.0"
language = "{language}"
production_paths = ["src"]
high_risk_paths = []

[[gates]]
adapter = "{complexity}"
paths = ["src"]

[[gates]]
adapter = "{architecture}"
paths = ["src"]

[complexity]
adapter = "{complexity}"
maximum = 10
'''.encode()
    )


def _evaluate(sources: dict[str, str], language: str = "python"):
    adapter = (
        "python.import-linter.v1" if language == "python" else "typescript.import-boundaries.v1"
    )
    return evaluate_architecture(
        _policy(language),
        {path: source.encode() for path, source in sources.items()},
        GateAdapter(adapter, ("src",)),
    )


def test_valid_layered_python_graph_passes() -> None:
    result = _evaluate(
        {
            "src/domain/model.py": "VALUE = 1\n",
            "src/application/use_case.py": "from domain.model import VALUE\n",
            "src/infrastructure/repository.py": "from domain.model import VALUE\n",
            "src/presentation/cli.py": "from application.use_case import VALUE\n",
        }
    )

    assert result.executed is True
    assert result.blocks == ()
    assert len(result.edges) == 3


def test_valid_layered_typescript_graph_passes() -> None:
    result = _evaluate(
        {
            "src/domain/model.ts": "export const value = 1;\n",
            "src/domain/native.mts": "export const native = 2;\n",
            "src/domain/legacy.cts": "export const legacy = 3;\n",
            "src/domain/view/index.tsx": "export const view = 4;\n",
            "src/application/useCase.ts": (
                "import { value } from '../domain/model.js';\n"
                "import { native } from '../domain/native.mjs';\n"
                "import { legacy } from '../domain/legacy.cjs';\n"
                "import { view } from '../domain/view.js';\n"
            ),
            "src/infrastructure/repository.ts": "import { value } from '../domain/model';\n",
            "src/presentation/view.ts": "import { value } from '../application/useCase';\n",
        },
        "typescript",
    )

    assert result.executed is True
    assert result.blocks == ()
    assert len(result.edges) == 6


def test_python_cycle_blocks() -> None:
    result = _evaluate(
        {
            "src/domain/a.py": "from domain.b import value\n",
            "src/domain/b.py": "from domain.a import value\n",
        }
    )

    assert sum(block.startswith("IMPORT_CYCLE:") for block in result.blocks) == 2


def test_typescript_cycle_blocks() -> None:
    result = _evaluate(
        {
            "src/domain/a.ts": "import { b } from './b';\nexport const a = b;\n",
            "src/domain/b.ts": "import { a } from './a';\nexport const b = a;\n",
        },
        "typescript",
    )

    assert sum(block.startswith("IMPORT_CYCLE:") for block in result.blocks) == 2


def test_cross_layer_inversion_blocks() -> None:
    result = _evaluate(
        {
            "src/application/use_case.py": "from infrastructure.repository import save\n",
            "src/infrastructure/repository.py": "def save(): pass\n",
        }
    )

    assert any(block.startswith("DEPENDENCY_INVERSION:") for block in result.blocks)


def test_domain_to_infrastructure_and_presentation_block() -> None:
    result = _evaluate(
        {
            "src/domain/model.py": (
                "from infrastructure.repository import save\nfrom presentation.view import render\n"
            ),
            "src/infrastructure/repository.py": "def save(): pass\n",
            "src/presentation/view.py": "def render(): pass\n",
        }
    )

    assert sum(block.startswith("FORBIDDEN_DOMAIN_DEPENDENCY:") for block in result.blocks) == 2


def test_domain_to_external_package_blocks() -> None:
    result = _evaluate(
        {"src/domain/model.py": "import fastapi\nimport sqlite3\nimport typing\n"}
    )
    node = _evaluate(
        {"src/domain/model.ts": "import { readFile } from 'node:fs';\n"}, "typescript"
    )

    assert result.blocks == (
        "FORBIDDEN_DOMAIN_DEPENDENCY:src/domain/model.py:1:fastapi",
        "FORBIDDEN_DOMAIN_DEPENDENCY:src/domain/model.py:2:sqlite3",
    )
    assert node.blocks == ("FORBIDDEN_DOMAIN_DEPENDENCY:src/domain/model.ts:1:node:fs",)
    assert _layer("domain/model.py", ("domain",)) == "domain"


def test_declared_but_unexecuted_architecture_gate_blocks() -> None:
    result = evaluate_architecture(_policy(), {"src/domain/model.py": b"VALUE = 1\n"}, None)

    assert result.executed is False
    assert result.blocks == ("ARCHITECTURE_GATE_NOT_EXECUTED",)


def test_incomplete_production_path_coverage_blocks() -> None:
    result = evaluate_architecture(
        _policy(),
        {
            "src/domain/model.py": b"VALUE = 1\n",
            "src/application/use_case.py": b"from domain.model import VALUE\n",
        },
        GateAdapter("python.import-linter.v1", ("src/domain",)),
    )

    assert result.blocks == ("ARCHITECTURE_PRODUCTION_COVERAGE:src/application/use_case.py",)


def test_architecture_evidence_is_deterministic() -> None:
    sources = {
        "src/domain/model.py": "VALUE = 1\n",
        "src/application/use_case.py": "from domain.model import VALUE\n",
    }

    assert _evaluate(sources) == _evaluate(dict(reversed(tuple(sources.items()))))


def test_same_line_import_edges_have_total_order() -> None:
    result = _evaluate(
        {
            "src/domain/a.py": "VALUE = 1\n",
            "src/domain/b.py": "VALUE = 2\n",
            "src/application/use_case.py": "from domain import b, a\n",
        }
    )

    assert [edge.target for edge in result.edges] == ["src/domain/a.py", "src/domain/b.py"]
