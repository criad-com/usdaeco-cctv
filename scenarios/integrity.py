"""Scenario adapters for the executable regression probes shared with pytest.

The family gate installs pytest with the library's test dependencies. Calling
these assertion functions keeps the negative scenarios and unit regressions
on the same fixtures; exceptions become failed scenario records in run.py.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
IMPORT_CASES = {'V-camera-no-optics', 'V-av-not-camera', 'V-fixed-varifocal', 'V-exchange-units'}


def run_probe(case_id, directory, kernel):
    sys.path.insert(0, str(ROOT / 'testenv'))
    import pytest
    import test_integrity as integrity
    import test_output_safety as safety
    import test_sampling as sampling
    import test_topology as topology
    from usdaeco_cctv.raycast import NumpyKernel, EmbreeKernel, embree_available
    engine = EmbreeKernel if kernel == 'embree' or (kernel == 'auto' and embree_available()) else NumpyKernel
    count = 0
    def invoke(function, *args, patch=False, **kwargs):
        nonlocal count
        folder = (Path(directory) / (case_id + '-' + str(count))).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if patch:
            with pytest.MonkeyPatch.context() as monkeypatch:
                function(folder, monkeypatch, *args, **kwargs)
        else:
            function(folder, *args, **kwargs)
        count += 1
    if case_id == 'V-results-incomplete':
        for fault in ('missing','deactivated','duplicate','dangling','nan','fraction','duty','level','fixed'):
            invoke(integrity.test_result_completeness, fault)
    elif case_id == 'V-two-studies':
        invoke(integrity.test_two_studies_order_and_shells, kernel=kernel)
        invoke(integrity.test_camera_scope_and_mount_band)
        integrity.test_density_settings_are_explicit()
        count += 1
    elif case_id == 'V-output-alias':
        for operation in ('derive','study'):
            for alias in ('same','symlink','sublayer'):
                invoke(safety.test_output_alias_refused, operation, alias)
            invoke(safety.test_failed_publish_preserves_previous, operation, patch=True)
        invoke(safety.test_study_in_stack_refused_even_when_owned)
        safety.test_anonymous_input_refused()
        count += 1
    elif case_id in ('V-hole','V-concave'):
        topology.test_notch_and_all_hole(engine, case_id == 'V-hole')
        count += 1
    elif case_id == 'V-malformed-mesh':
        for fault in ('index','count','bowtie','degenerate'):
            topology.test_malformed_mesh_refused(engine, fault)
            count += 1
    elif case_id == 'V-stale-matrix':
        for change in ('driver','phase','geometry','collection','ir','algorithm','version','noop'):
            invoke(integrity.test_stale_matrix, change, patch=True)
    elif case_id == 'V-extent-target':
        sampling.test_extent_is_sample_source_never_obstacle()
        count += 1
        invoke(sampling.test_extent_target_study, kernel)
    elif case_id == 'V-enclosed-samples':
        import test_enclosed as enclosed
        for enclosed_count in (1, 3, 5):
            invoke(enclosed.test_lobby_crate, kernel, enclosed_count)
        invoke(enclosed.test_yard_grid, kernel)
        for shape in ('closed', 'open_top', 'two_open_axes', 'plane', 'hollow', 'overlap', 'remote'):
            enclosed.test_axis_parity(kernel, shape)
            count += 1
        enclosed.test_surface_and_overlapping_gprims(kernel)
        count += 1
    elif case_id in IMPORT_CASES:
        import test_exchange as exchange
        if case_id in ('V-camera-no-optics','V-av-not-camera'):
            invoke(exchange.test_exchange_refusals_and_precedence,
                   'no-optics' if case_id == 'V-camera-no-optics' else 'not-camera')
        elif case_id == 'V-fixed-varifocal':
            invoke(exchange.test_fixed_varifocal_is_fixed_coverage, kernel=kernel)
        else:
            for mm, rad in ((True,False),(False,False),(True,True),(False,True)):
                for tier_a in (True,False):
                    invoke(exchange.test_exchange_units, mm, rad, tier_a)
    else:
        raise ValueError('unknown integrity probe ' + case_id)
    return {'id': case_id, 'passed': True, 'failures': [], 'probes': count, 'kernel': engine.name}
