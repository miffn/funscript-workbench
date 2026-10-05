"""A new NAS folder must not probe every old filesystem binding."""
from pathlib import Path

from backend.scanner import existing_path_owner


def test_unrelated_paths_do_not_issue_filesystem_requests(monkeypatch):
    def unexpected_probe(path):
        raise AssertionError('An unrelated old directory triggered filesystem I/O')
    monkeypatch.setattr('backend.scan_roots.no_link_components', unexpected_probe)
    bindings = {f'/mnt/d/library/Work-{number}': number for number in range(1, 501)}
    assert existing_path_owner(Path('/mnt/z/materials/New work'), bindings) is None


def test_exact_binding_needs_no_filesystem_probe(monkeypatch):
    def unexpected_probe(path):
        raise AssertionError('An exact path triggered an alias probe')
    monkeypatch.setattr('backend.scan_roots.no_link_components', unexpected_probe)
    path = Path('/mnt/z/materials/Known work')
    assert existing_path_owner(path, {str(path): 17}) == 17


def test_case_only_spelling_still_checks_real_file_identity(tmp_path):
    first, second = tmp_path / 'Foo', tmp_path / 'foo'
    first.mkdir()
    second.mkdir()
    assert existing_path_owner(second, {str(first): 17}) is None
