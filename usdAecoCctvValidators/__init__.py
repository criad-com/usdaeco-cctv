"""The 23 CCTV rules, exposed as a Python UsdValidation plugin."""
from pathlib import Path
import os
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "tools"), str(Path(os.environ.get("TOOLCHAIN_DIR", ROOT.parent / "usdaeco-toolchain")) / "tools")]
from usdaeco_cctv import validators as legacy
from usdaeco_check.validation import register_prim_validator, register_stage_validator, wrap_legacy
from . import validatorTokens as tokens
register_prim_validator(tokens.CCTVINVALIDSAMPLING_CHECKER, wrap_legacy(tokens.CCTVINVALIDSAMPLING_CHECKER, lambda target: legacy._sampling(target, None)), ['UsdGeomImageable'])
register_prim_validator(tokens.CCTVMISSINGOPTICS_CHECKER, wrap_legacy(tokens.CCTVMISSINGOPTICS_CHECKER, lambda target: legacy._missing_optics(target, None)), ['UsdAecoCctvSensorAPI'])
register_prim_validator(tokens.CCTVUNSUPPORTEDTOPOLOGY_CHECKER, wrap_legacy(tokens.CCTVUNSUPPORTEDTOPOLOGY_CHECKER, lambda target: legacy._topology(target, None)), ['UsdGeomMesh'])
register_prim_validator(tokens.CCTVKINDMISMATCH_CHECKER, wrap_legacy(tokens.CCTVKINDMISMATCH_CHECKER, lambda target: legacy._kind(target, None)), ['UsdAecoCctvCameraAPI'])
register_prim_validator(tokens.CCTVSENSORMISSING_CHECKER, wrap_legacy(tokens.CCTVSENSORMISSING_CHECKER, lambda target: legacy._sensor_missing(target, None)), ['UsdAecoCctvCameraAPI'])
register_prim_validator(tokens.CCTVSENSORORPHAN_CHECKER, wrap_legacy(tokens.CCTVSENSORORPHAN_CHECKER, lambda target: legacy._sensor_orphan(target, None)), ['UsdAecoCctvSensorAPI'])
register_prim_validator(tokens.CCTVOUTOFENVELOPE_CHECKER, wrap_legacy(tokens.CCTVOUTOFENVELOPE_CHECKER, lambda target: legacy._envelope(target, None)), ['UsdAecoCctvSensorAPI'])
register_prim_validator(tokens.CCTVNATIVECAMERAAUTHORED_CHECKER, wrap_legacy(tokens.CCTVNATIVECAMERAAUTHORED_CHECKER, lambda target: legacy._native(target, None)), ['UsdAecoCctvSensorAPI'])
register_prim_validator(tokens.CCTVDERIVEDMISMATCH_CHECKER, wrap_legacy(tokens.CCTVDERIVEDMISMATCH_CHECKER, lambda target: legacy._mismatch(target, None)), ['UsdAecoCctvSensorAPI'])
register_stage_validator(tokens.CCTVMOUNTFRAME_CHECKER, wrap_legacy(tokens.CCTVMOUNTFRAME_CHECKER, lambda target: legacy._mount_frame(target, None)))
register_prim_validator(tokens.CCTVSYSTEMCAPACITY_CHECKER, wrap_legacy(tokens.CCTVSYSTEMCAPACITY_CHECKER, lambda target: legacy._system_capacity(target, None)), ['UsdAecoCctvSystemAPI'])
register_prim_validator(tokens.CCTVUNSUPPORTEDPROJECTION_CHECKER, wrap_legacy(tokens.CCTVUNSUPPORTEDPROJECTION_CHECKER, lambda target: legacy._projection(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVSTUDYINCOMPLETE_CHECKER, wrap_legacy(tokens.CCTVSTUDYINCOMPLETE_CHECKER, lambda target: legacy._incomplete(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVUNPHASEDPROVIDER_CHECKER, wrap_legacy(tokens.CCTVUNPHASEDPROVIDER_CHECKER, lambda target: legacy._unphased_provider(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVSTUDYMISSINGRESULTS_CHECKER, wrap_legacy(tokens.CCTVSTUDYMISSINGRESULTS_CHECKER, lambda target: legacy._missing_results(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVSTUDYSTALE_CHECKER, wrap_legacy(tokens.CCTVSTUDYSTALE_CHECKER, lambda target: legacy._stale(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVTARGETUNCOVERED_CHECKER, wrap_legacy(tokens.CCTVTARGETUNCOVERED_CHECKER, lambda target: legacy._uncovered(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVTARGETMOSTLYENCLOSED_CHECKER, wrap_legacy(tokens.CCTVTARGETMOSTLYENCLOSED_CHECKER, lambda target: legacy._mostly_enclosed(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVPTZSOLECOVERAGE_CHECKER, wrap_legacy(tokens.CCTVPTZSOLECOVERAGE_CHECKER, lambda target: legacy._ptz_sole(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVTARGETTOOFAR_CHECKER, wrap_legacy(tokens.CCTVTARGETTOOFAR_CHECKER, lambda target: legacy._too_far(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVEXCLUSIONCOVERED_CHECKER, wrap_legacy(tokens.CCTVEXCLUSIONCOVERED_CHECKER, lambda target: legacy._exclusion_covered(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVUNPHASEDINVIEW_CHECKER, wrap_legacy(tokens.CCTVUNPHASEDINVIEW_CHECKER, lambda target: legacy._unphased(target, None)), ['UsdAecoCctvStudyAPI'])
register_prim_validator(tokens.CCTVUNCLASSIFIEDINVIEW_CHECKER, wrap_legacy(tokens.CCTVUNCLASSIFIEDINVIEW_CHECKER, lambda target: legacy._unclassified(target, None)), ['UsdAecoCctvStudyAPI'])
