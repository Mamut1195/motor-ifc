"""IDS validation against the real ifctester, end to end.

`ids_validation.py` genuinely integrates ifctester — it imports the module,
pins 0.8.5 and calls `tester.open -> specs.validate -> reporter.Json`. What
did not exist was any evidence that it works: every test in
`tests/test_ids_validation.py` substitutes the runtime with a SimpleNamespace,
so the report keys the engine depends on (`total_checks_pass`, `is_skipped`,
`total_applicable_fail`, ...) were an unverified assumption that the mock
reproduced by construction. CI installed `.[test,ids]` and never ran it.

These tests carry the `ids` marker, which was declared in pyproject.toml and
used by no test at all.
"""

from __future__ import annotations

import pytest

from motor_ifc import validate_ids
from motor_ifc.diagnostics import DiagnosticCode

pytestmark = [pytest.mark.ids, pytest.mark.ifcopenshell]

IDS_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS"
     xmlns:xs="http://www.w3.org/2001/XMLSchema"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:schemaLocation="http://standards.buildingsmart.org/IDS
                         http://standards.buildingsmart.org/IDS/1.0/ids.xsd">
  <info>
    <title>{title}</title>
  </info>
  <specifications>
    <specification name="{name}" ifcVersion="IFC4">
      <applicability minOccurs="1" maxOccurs="unbounded">
        <entity><name><simpleValue>IFCWALL</simpleValue></name></entity>
      </applicability>
      <requirements>
        <attribute>
          <name><simpleValue>Name</simpleValue></name>
        </attribute>
      </requirements>
    </specification>
  </specifications>
</ids>
"""


@pytest.fixture
def ids_file(tmp_path):
    def _write(title="Minimal IDS", name="Walls carry a name"):
        target = tmp_path / "requirements.ids"
        target.write_text(IDS_HEADER.format(title=title, name=name), encoding="utf-8")
        return target

    return _write


@pytest.fixture
def ifc_file(tmp_path):
    """A real IFC4 file with one wall, written by ifcopenshell itself."""
    ifcopenshell = pytest.importorskip("ifcopenshell")

    def _write(*, wall_name="Muro 1"):
        model = ifcopenshell.file(schema="IFC4")
        model.create_entity(
            "IfcWall",
            GlobalId=ifcopenshell.guid.new(),
            Name=wall_name,
        )
        target = tmp_path / f"model-{wall_name or 'unnamed'}.ifc"
        model.write(str(target))
        return target

    return _write


class TestTheRealRuntimeIsReachable:
    def test_the_pinned_runtime_loads(self):
        from motor_ifc.ids_validation import runtime

        ifc, tester, reporter = runtime()

        assert ifc.version == "0.8.5"
        assert tester.__version__ == "0.8.5"
        assert hasattr(reporter, "Json")


class TestReportShapeIsWhatTheEngineAssumes:
    def test_a_satisfied_requirement_reports_valid(self, ifc_file, ids_file):
        result = validate_ids(ifc_file(wall_name="Muro 1"), ids_file())

        assert result.success, result.diagnostics
        assert result.valid is True
        assert result.ids_title == "Minimal IDS"
        assert result.diagnostics == ()

    def test_an_unsatisfied_requirement_reports_invalid_not_failed(
        self, ifc_file, ids_file
    ):
        """A failing IFC is a valid=False answer, not a broken validation."""
        result = validate_ids(ifc_file(wall_name=None), ids_file())

        assert result.success, result.diagnostics
        assert result.valid is False
        assert [d.code for d in result.diagnostics] == [
            int(DiagnosticCode.IDS_REQUIREMENTS_FAILED)
        ]

    def test_the_summary_keys_the_engine_reads_are_really_produced(
        self, ifc_file, ids_file
    ):
        """The consistency block cross-checks nine counters against each other.

        If ifctester renamed or dropped any of them, the engine would fall
        into "report is inconsistent" — so reaching a coherent summary is
        itself the proof that the assumed key names are real.
        """
        result = validate_ids(ifc_file(), ids_file())

        assert result.success, result.diagnostics
        summary = result.summary
        assert summary.specifications == 1
        assert summary.specifications_passed + summary.specifications_failed == 1
        assert summary.requirements == 1
        assert summary.requirements_passed + summary.requirements_failed == 1
        assert summary.checks_passed + summary.checks_failed == summary.checks

    def test_per_specification_rows_line_up_with_the_summary(self, ifc_file, ids_file):
        result = validate_ids(ifc_file(), ids_file())

        assert len(result.specifications) == 1
        row = result.specifications[0]
        assert row.index == 1
        assert row.name == "Walls carry a name"
        assert row.requirements == 1
        assert row.checks_passed + row.checks_failed == row.checks
        assert row.applicable_entities >= 1
        assert row.skipped is False


class TestMalformedInputStillFailsTyped:
    def test_ids_that_is_not_schema_valid_is_reported_as_invalid_ids(
        self, tmp_path, ifc_file
    ):
        broken = tmp_path / "broken.ids"
        broken.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ids xmlns="http://standards.buildingsmart.org/IDS">'
            "<specifications><specification/></specifications></ids>",
            encoding="utf-8",
        )

        result = validate_ids(ifc_file(), broken)

        assert not result.success
        assert [d.code for d in result.diagnostics] == [int(DiagnosticCode.INVALID_IDS)]
