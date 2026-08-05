from pathlib import Path
from types import SimpleNamespace
import pytest
from motor_ifc import validate_ids
from motor_ifc import ids_validation

IDS_XML="""<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:schemaLocation="http://standards.buildingsmart.org/IDS http://standards.buildingsmart.org/IDS/1.0/ids.xsd">
  <info><title>Caller requirements</title></info>
  <specifications><specification name="Project name" ifcVersion="IFC4">
    <applicability minOccurs="1" maxOccurs="unbounded"><entity><name><simpleValue>IFCPROJECT</simpleValue></name></entity></applicability>
    <requirements><attribute cardinality="required"><name><simpleValue>Name</simpleValue></name><value><simpleValue>Expected project</simpleValue></value></attribute></requirements>
  </specification></specifications>
</ids>
"""

def _inputs(tmp_path: Path)->tuple[Path,Path]:
    ifc=tmp_path/"model.ifc"; ifc.write_text("IFC",encoding="utf-8")
    ids=tmp_path/"requirements.ids"; ids.write_text(IDS_XML,encoding="utf-8")
    return ifc,ids

def _report(passed: bool)->dict:
    return {
        "title":"Caller requirements","status":passed,
        "total_specifications":1,"total_specifications_pass":int(passed),"total_specifications_fail":int(not passed),
        "total_requirements":1,"total_requirements_pass":int(passed),"total_requirements_fail":int(not passed),
        "total_checks":1,"total_checks_pass":int(passed),"total_checks_fail":int(not passed),
        "specifications":[{"name":"Project name","status":passed,"is_skipped":False,"total_applicable":1,
            "total_applicable_pass":int(passed),"total_applicable_fail":int(not passed),"requirements":[{}],
            "total_checks":1,"total_checks_pass":int(passed),"total_checks_fail":int(not passed)}],
    }

def _runtime(report: dict):
    specification=SimpleNamespace(requirements=[object()])
    specs=SimpleNamespace(specifications=[specification],validate=lambda model: None)
    tester=SimpleNamespace(open=lambda path,validate: specs)
    ifc=SimpleNamespace(open=lambda path: object())
    reporter=SimpleNamespace(Json=lambda loaded: SimpleNamespace(report=lambda: report))
    return ifc,tester,reporter

@pytest.mark.parametrize("passed",[True,False],ids=["pass","fail"])
def test_ids_validation_returns_bounded_stable_report(tmp_path,monkeypatch,passed):
    ifc,ids=_inputs(tmp_path); report=_report(passed)
    monkeypatch.setattr(ids_validation,"runtime",lambda: _runtime(report))
    first=validate_ids(ifc,ids); second=validate_ids(ifc,ids)
    assert first==second
    assert first.success and first.valid is passed and first.contract_version=="ids-validation.v1"
    assert first.publication=="none" and first.artifact_filenames==()
    assert first.summary.specifications==1 and first.summary.checks==1
    assert first.specifications[0].model_dump()=={
        "index":1,"name":"Project name","passed":passed,"skipped":False,"applicable_entities":1,
        "applicable_entities_passed":int(passed),"applicable_entities_failed":int(not passed),
        "requirements":1,"checks":1,"checks_passed":int(passed),"checks_failed":int(not passed),
    }
    assert [item.code for item in first.diagnostics]==([] if passed else [2604])

def test_ids_validation_rejects_malformed_and_unsupported_xml(tmp_path):
    ifc,ids=_inputs(tmp_path)
    ids.write_text("<ids",encoding="utf-8")
    assert validate_ids(ifc,ids).diagnostics[0].code==2601
    ids.write_text("<ids xmlns='https://example.test/ids'/>",encoding="utf-8")
    assert validate_ids(ifc,ids).diagnostics[0].code==2602

def test_ids_validation_rejects_dtd_before_optional_runtime(tmp_path):
    ifc,ids=_inputs(tmp_path)
    ids.write_text("<!DOCTYPE ids [<!ENTITY x 'x'>]><ids xmlns='http://standards.buildingsmart.org/IDS'/>",encoding="utf-8")
    assert validate_ids(ifc,ids).diagnostics[0].code==2601

def test_ids_validation_rejects_utf16_dtd_and_entity_before_optional_runtime(tmp_path,monkeypatch):
    ifc,ids=_inputs(tmp_path)
    xml="""<?xml version="1.0" encoding="UTF-16"?>
<!DOCTYPE ids [<!ENTITY title "expanded entity">]>
<ids xmlns="http://standards.buildingsmart.org/IDS"><info><title>&title;</title></info><specifications/></ids>"""
    ids.write_bytes(xml.encode("utf-16"))
    monkeypatch.setattr(ids_validation,"runtime",lambda: pytest.fail("runtime must not parse declared entities"))
    result=validate_ids(ifc,ids)
    assert not result.success and result.ids_title is None
    assert result.diagnostics[0].code==2601
    assert "expanded entity" not in result.model_dump_json()

def test_ids_validation_preserves_legitimate_utf16_xml(tmp_path,monkeypatch):
    ifc,ids=_inputs(tmp_path)
    ids.write_bytes(IDS_XML.replace('encoding="UTF-8"','encoding="UTF-16"').encode("utf-16"))
    monkeypatch.setattr(ids_validation,"runtime",lambda: _runtime(_report(True)))
    result=validate_ids(ifc,ids)
    assert result.success and result.valid

def test_ids_validation_optional_runtime_failure_is_typed(tmp_path,monkeypatch):
    ifc,ids=_inputs(tmp_path)
    def unavailable(): raise ids_validation.IdsRuntimeUnavailable("private detail")
    monkeypatch.setattr(ids_validation,"runtime",unavailable)
    result=validate_ids(ifc,ids)
    assert not result.success and not result.valid
    assert result.diagnostics[0].code==2600
    assert "private detail" not in result.model_dump_json()

def test_ids_validation_rejects_oversize_input(tmp_path,monkeypatch):
    ifc,ids=_inputs(tmp_path)
    monkeypatch.setattr(ids_validation,"MAX_IDS_BYTES",len(IDS_XML.encode("utf-8"))-1)
    result=validate_ids(ifc,ids)
    assert not result.success and result.diagnostics[0].code==2003

def test_ids_validation_rejects_report_bounds(tmp_path,monkeypatch):
    ifc,ids=_inputs(tmp_path); report=_report(True); report["total_checks"]=ids_validation.MAX_CHECKS+1
    monkeypatch.setattr(ids_validation,"runtime",lambda: _runtime(report))
    result=validate_ids(ifc,ids)
    assert not result.success and result.diagnostics[0].code==2003

def test_ids_validation_rejects_oversized_reporter_rows(tmp_path,monkeypatch):
    ifc,ids=_inputs(tmp_path); report=_report(True)
    report["specifications"]*=ids_validation.MAX_SPECIFICATIONS+1
    monkeypatch.setattr(ids_validation,"runtime",lambda: _runtime(report))
    result=validate_ids(ifc,ids)
    assert not result.success and result.specifications==()
    assert result.diagnostics[0].code==2003

def test_ids_validation_rejects_inconsistent_reporter_rows(tmp_path,monkeypatch):
    ifc,ids=_inputs(tmp_path); report=_report(True)
    report["specifications"].append(dict(report["specifications"][0]))
    monkeypatch.setattr(ids_validation,"runtime",lambda: _runtime(report))
    result=validate_ids(ifc,ids)
    assert not result.success and result.specifications==()
    assert result.diagnostics[0].code==2603
    assert "requirements.ids" not in result.model_dump_json()

def test_ids_validation_does_not_publish_or_mutate_inputs(tmp_path,monkeypatch):
    ifc,ids=_inputs(tmp_path); sentinel=tmp_path/"manifest.json"; sentinel.write_text("unchanged",encoding="utf-8")
    before={path.name:(path.read_bytes(),path.stat().st_mtime_ns) for path in tmp_path.iterdir()}
    monkeypatch.setattr(ids_validation,"runtime",lambda: _runtime(_report(True)))
    assert validate_ids(ifc,ids).valid
    after={path.name:(path.read_bytes(),path.stat().st_mtime_ns) for path in tmp_path.iterdir()}
    assert after==before

def test_ids_capability_is_explicit():
    from motor_ifc import capabilities
    result=capabilities()
    assert result.ids_validation_contract_versions==("ids-validation.v1",)
    assert result.ids_versions==("1.0",)
