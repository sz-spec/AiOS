"""Execute the production root synchronizer on adversarial page-table fixtures.

The previous source-string tests mandated boot-global CR3 and task-stack access
before switching roots, both implicated in the native boot failure. Those
obsolete implementation pins are replaced by behavioral root-mapping tests.
Assembly transitions are separately exercised by native BIOS/UEFI and keyboard
setup gates; these host tests cannot certify privileged transition correctness.
"""
import ctypes
import pathlib
import random
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3] / 'kernel'
Page = ctypes.c_uint64 * 512


@pytest.fixture(scope='module')
def sync_root(tmp_path_factory):
    compiler = shutil.which('cc') or shutil.which('clang')
    if compiler is None:
        pytest.fail('A C compiler is required to test the production KPTI root synchronizer')
    library = tmp_path_factory.mktemp('kpti-roots') / 'roots.so'
    subprocess.run([compiler, '-std=c11', '-O2', '-shared', '-fPIC', '-Werror',
                    '-I', str(ROOT / 'include'),
                    str(ROOT / 'src/arch/x86_64/kpti_roots.c'), '-o', str(library)], check=True)
    function = ctypes.CDLL(str(library)).vos3_kpti_sync_root
    function.argtypes = [ctypes.POINTER(ctypes.c_uint64)] * 2
    function.restype = ctypes.c_int
    return function


@pytest.mark.parametrize('seed', range(16))
def test_process_mappings_survive_without_cross_process_contamination(sync_root, seed):
    rng = random.Random(seed)
    parents = [Page(*(rng.getrandbits(64) for _ in range(512))) for _ in range(2)]
    children = [Page(*([0xFFFFFFFFFFFFFFFF] * 512)) for _ in range(2)]
    original = [list(p) for p in parents]
    for owner in [0, 1, 0, 1]:
        other_before = list(children[1 - owner])
        assert sync_root(children[owner], parents[owner]) == 0
        assert list(children[owner])[:256] == original[owner][:256]
        assert children[owner][256] == original[owner][256]
        assert children[owner][511] == original[owner][511]
        assert list(children[owner])[257:511] == [0] * 254
        assert list(children[1 - owner]) == other_before
        assert [list(p) for p in parents] == original


def test_revoked_mapping_and_poisoned_kernel_slot_do_not_survive_resync(sync_root):
    full, user = Page(), Page()
    full[7] = 0xABCDE007
    assert sync_root(user, full) == 0
    assert user[7] == full[7]
    full[7] = 0
    user[384] = 0xBAD007  # Dynamic kernel stack region must not survive.
    assert sync_root(user, full) == 0
    assert user[7] == 0
    assert user[384] == 0


@pytest.mark.parametrize('offset', [0, 8, 2048, 4088])
@pytest.mark.parametrize('reverse', [False, True])
def test_aliasing_roots_are_rejected_without_modifying_memory(sync_root, offset, reverse):
    backing = (ctypes.c_uint64 * 1024)(*range(1024))
    before = bytes(backing)
    a = ctypes.cast(backing, ctypes.POINTER(ctypes.c_uint64))
    b = ctypes.cast(ctypes.byref(backing, offset), ctypes.POINTER(ctypes.c_uint64))
    result = sync_root(b, a) if reverse else sync_root(a, b)
    assert result == -1
    assert bytes(backing) == before


@pytest.mark.parametrize('missing_destination', [False, True])
def test_missing_root_is_rejected_without_modification(sync_root, missing_destination):
    page = Page(*range(512))
    before = bytes(page)
    assert (sync_root(None, page) if missing_destination else sync_root(page, None)) == -1
    assert bytes(page) == before
