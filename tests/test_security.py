from pathlib import Path
import pytest
from motor_ifc.security import UnsafePathError, rpc_input, rpc_output, secure_new_output

def test_output_root_rejects_parent_traversal(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UnsafePathError): secure_new_output(Path("child")/".."/"escape")

def test_output_root_rejects_existing_path(tmp_path):
    path=tmp_path/"result"; path.mkdir()
    with pytest.raises(UnsafePathError): secure_new_output(path)

def test_output_root_rejects_symlink_when_supported(tmp_path):
    real=tmp_path/"real"; real.mkdir(); link=tmp_path/"link"
    try: link.symlink_to(real,target_is_directory=True)
    except OSError: pytest.skip("symlink creation unavailable on this platform")
    with pytest.raises(UnsafePathError): secure_new_output(link)

def test_rpc_output_requires_relative_path(tmp_path):
    with pytest.raises(UnsafePathError): rpc_output(tmp_path,str(tmp_path/"absolute"))
    with pytest.raises(UnsafePathError): rpc_output(tmp_path,"../escape")

@pytest.mark.parametrize("relative",["C:drive.glb","name:ads","safe/name:ads","CON.foo/result","safe/COM1.extra.glb","safe/\x7f","safe/\x85"])
def test_rpc_paths_reject_unsafe_components(tmp_path,relative):
    with pytest.raises(UnsafePathError): rpc_output(tmp_path,relative)

def test_rpc_output_preserves_valid_unicode_components(tmp_path):
    (tmp_path/"données").mkdir()
    assert rpc_output(tmp_path,"données/模型")==tmp_path.resolve()/"données"/"模型"

def test_rpc_input_requires_contained_regular_file(tmp_path):
    path=tmp_path/"model.ifc"; path.write_text("IFC",encoding="utf-8")
    assert rpc_input(tmp_path,"model.ifc",10)==path.resolve()
    with pytest.raises(UnsafePathError): rpc_input(tmp_path,"../escape.ifc",10)
    with pytest.raises(UnsafePathError): rpc_input(tmp_path,"missing.ifc",10)
    with pytest.raises(UnsafePathError): rpc_input(tmp_path,"model.ifc",2)

def test_rpc_input_rejects_symlink_when_supported(tmp_path):
    target=tmp_path/"target.ifc"; target.write_text("IFC",encoding="utf-8"); link=tmp_path/"link.ifc"
    try: link.symlink_to(target)
    except OSError: pytest.skip("symlink creation unavailable on this platform")
    with pytest.raises(UnsafePathError): rpc_input(tmp_path,"link.ifc",10)
