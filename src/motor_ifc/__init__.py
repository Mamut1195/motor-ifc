"""Public motor-ifc API."""
from .api import build_federation, capabilities, compile_snapshot, convert_ifc_to_glb, extract_ifc, inspect_ifc, validate_ids, validate_ifc, validate_snapshot
from .models import CompileContext, IdsSpecificationResult, IdsValidationResult, IdsValidationSummary, ReaderEntity, ReaderEntityMetadata, ReaderEntityProperties, ReaderEntityQuantities, ReaderExtractionResult, ValidationPolicy, ViewerConversionResult
__all__=["CompileContext","IdsSpecificationResult","IdsValidationResult","IdsValidationSummary","ReaderEntity","ReaderEntityMetadata","ReaderEntityProperties","ReaderEntityQuantities","ReaderExtractionResult","ValidationPolicy","ViewerConversionResult","build_federation","capabilities","compile_snapshot","convert_ifc_to_glb","extract_ifc","inspect_ifc","validate_ids","validate_ifc","validate_snapshot"]
from ._version import VERSION as __version__
