from prereview.artifact.model import Work, WorkFile
from prereview.checks.primitives import file_exists, grep, mermaid_blocks, section_exists
from prereview.checks.run import run_checks
from prereview.rubric.model import CheckSpec, Criterion
import uuid


def repo():
    return Work("zip", "application/zip", [
        WorkFile("go.mod", "module x\n", "gomod"),
        WorkFile("cmd/app/main.go", "package main\nfunc main() { fmt.Println(1) }\n", "go"),
        WorkFile("internal/h/h.go", "package h\n// x\nfunc H() {}\n", "go"),
        WorkFile("README.md", "# Title\n## Связность\n```mermaid\nC4Context\n```\n", "markdown"),
    ])


def test_primitives():
    assert file_exists(repo(), "go.mod").status == "pass"
    assert file_exists(repo(), "Makefile").status == "fail"
    assert grep(repo(), r"fmt\.Print", scope=["internal/**/*.go"], expect="absent").status == "pass"
    assert grep(repo(), r"fmt\.Print", scope=["**/*.go"], expect="absent").status == "fail"
    assert section_exists(repo(), "связност").status == "pass"
    out = mermaid_blocks(repo(), 1, ["C4Context", "C4Component"])
    assert out.status == "fail" and "C4Component" in out.note


def test_run_checks_error_is_not_fail():
    c = Criterion(id=uuid.uuid4(), key="x", title="x", max_points=1, check_class="formal",
                  checks=[CheckSpec(kind="no_such_primitive")])
    assert run_checks(repo(), c).status == "error"
