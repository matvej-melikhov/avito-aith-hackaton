from prereview.artifact.model import Work, WorkFile
from prereview.judge.schema import EvidenceItem
from prereview.judge.verify import verify_quote

TEXT = "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(\"hi\")\n}\n"


def work():
    return Work("zip", "application/zip", [WorkFile("cmd/main.go", TEXT, "go"), WorkFile("README.md", "# T\nСервис\n", "markdown")])


def test_exact():
    v = verify_quote(work(), EvidenceItem(path="cmd/main.go", line_start=5, line_end=6, quote="func main() {\n\tfmt.Println(\"hi\")"))
    assert v.status == "exact" and (v.line_start, v.line_end) == (5, 6)


def test_whitespace_and_wrong_lines_relocated():
    v = verify_quote(work(), EvidenceItem(path="cmd/main.go", line_start=1, line_end=1, quote="fmt.Println( \"hi\" )"))
    assert v.status == "relocated" and v.line_start == 6


def test_other_file():
    v = verify_quote(work(), EvidenceItem(path="cmd/main.go", line_start=1, line_end=1, quote="Сервис"))
    assert v.status == "relocated" and v.path == "README.md"


def test_dropped():
    v = verify_quote(work(), EvidenceItem(path="cmd/main.go", line_start=1, line_end=1, quote="этого текста нет"))
    assert v.status == "dropped" and not v.ok
