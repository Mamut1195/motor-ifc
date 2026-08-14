"""Two ways a file could be read or written outside the job root.

1. Three legacy RPC methods took their path through ``_required``, which
   only checks the key exists. The path reached ``ifc.open`` raw: absolute,
   ``..``, symlink, unbounded size. The equivalent ``*.v1`` methods were
   fixed to route through ``_contained_input`` and the fix was never
   propagated to ``LegacyAdapterV1``.

2. ``temp_root()`` returned None both when MOTOR_IFC_JOB_ROOT was unset and
   when it was set but unusable, and that None went straight into
   ``tempfile.TemporaryDirectory(dir=...)``, which falls back to the system
   %TEMP%. The two cases are not the same: configuring a job root is a
   request for containment, so failing to honour it silently wrote the full
   private snapshot of the IFC — up to 100 MB — outside the sandbox the
   caller asked for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from motor_ifc.rpc import RpcFault, dispatch

LEGACY_PATH_METHODS = ["inspect_ifc", "extract_spatial_tree", "validate_basic"]


@pytest.fixture
def job_root(tmp_path, monkeypatch):
    root = tmp_path / "job"
    root.mkdir()
    monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(root))
    return root


@pytest.fixture
def outside_file(tmp_path):
    target = tmp_path / "outside.ifc"
    target.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
    return target


class TestLegacyMethodsAreContained:
    @pytest.mark.parametrize("method", LEGACY_PATH_METHODS)
    def test_an_absolute_path_outside_the_root_is_refused(self, method, job_root, outside_file):
        with pytest.raises(RpcFault) as raised:
            dispatch(method, {"path": str(outside_file)}, str(job_root))

        assert raised.value.code == -32602

    @pytest.mark.parametrize("method", LEGACY_PATH_METHODS)
    def test_parent_traversal_is_refused(self, method, job_root, outside_file):
        with pytest.raises(RpcFault) as raised:
            dispatch(method, {"path": f"../{outside_file.name}"}, str(job_root))

        assert raised.value.code == -32602

    @pytest.mark.parametrize("method", LEGACY_PATH_METHODS)
    def test_a_missing_job_root_is_refused(self, method, outside_file):
        with pytest.raises(RpcFault) as raised:
            dispatch(method, {"path": str(outside_file)}, None)

        assert raised.value.code == -32602

    @pytest.mark.parametrize("method", LEGACY_PATH_METHODS)
    def test_a_contained_relative_path_is_accepted(self, method, job_root):
        inside = job_root / "model.ifc"
        inside.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")

        result = dispatch(method, {"path": "model.ifc"}, str(job_root))

        assert isinstance(result, dict)

    def test_get_version_needs_no_path_and_still_works(self, job_root):
        assert "version" in dispatch("get_version", {}, str(job_root))


class TestSnapshotsStayInsideTheJobRoot:
    """Configuring a job root is a request for containment.

    No job root at all is a different thing: nothing was promised, so the
    system default applies and the library stays usable standalone. What
    must never happen is a job root that was configured, could not be
    honoured, and was dropped without a word.
    """

    def test_no_job_root_means_the_system_default_not_an_error(self, monkeypatch):
        from motor_ifc.security import temp_root

        monkeypatch.delenv("MOTOR_IFC_JOB_ROOT", raising=False)

        assert temp_root() is None

    def test_a_job_root_that_is_not_a_directory_is_an_explicit_error(self, tmp_path, monkeypatch):
        from motor_ifc.security import JobRootUnavailable, temp_root

        not_a_directory = tmp_path / "file.txt"
        not_a_directory.write_text("x")
        monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(not_a_directory))

        with pytest.raises(JobRootUnavailable):
            temp_root()

    def test_a_job_root_that_does_not_exist_is_an_explicit_error(self, tmp_path, monkeypatch):
        from motor_ifc.security import JobRootUnavailable, temp_root

        monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(tmp_path / "missing"))

        with pytest.raises(JobRootUnavailable):
            temp_root()

    def test_a_configured_job_root_yields_a_contained_directory(self, job_root):
        from motor_ifc.security import temp_root

        target = temp_root()

        assert Path(target).is_dir()
        assert job_root.resolve() in Path(target).resolve().parents

    def test_an_unusable_job_root_fails_the_extraction_typed(self, tmp_path, monkeypatch):
        """The escape route, closed end to end: no snapshot lands in %TEMP%."""
        from motor_ifc import extract_ifc
        from motor_ifc.diagnostics import DiagnosticCode

        model = tmp_path / "model.ifc"
        model.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
        broken_root = tmp_path / "not-a-dir.txt"
        broken_root.write_text("x")
        monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(broken_root))

        result = extract_ifc(model)

        assert result.success is False
        assert [d.code for d in result.diagnostics] == [
            int(DiagnosticCode.JOB_ROOT_UNAVAILABLE)
        ]
