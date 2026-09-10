"""Smoke test for the test infrastructure itself (fixtures, safety guard)."""
from __future__ import annotations


def test_store_loads_real_data_via_tmp_copy(store):
    courses = store.list_courses()
    assert len(courses) == 6
    codes = {c.code for c in courses}
    assert codes == {"CMTC2341", "CNTR2371", "INST2310", "INST2340", "INST2361", "INST2380"}


def test_empty_store_has_no_courses(empty_store):
    assert empty_store.list_courses() == []
    assert empty_store.created_empty is True


def test_workbook_path_is_a_private_copy(workbook_path, tmp_path):
    assert workbook_path.parent == tmp_path
    assert workbook_path.exists()


def test_store_mutation_does_not_touch_real_file(store):
    from app.models import Course
    store.add_course(Course(code="ZZZZ", name="Should not leak anywhere"))
    store.save(force=True)
    # the real workbook is checked by the autouse _guard_real_workbook
    # fixture after the whole session; this test just needs to exist and
    # actually perform a save to a tmp copy to prove the guard would catch
    # a leak if `store` ever pointed at the real file by mistake.
    assert len(store.list_courses()) == 7
