"""Filesystem boundary contracts; no application or remote service is needed."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from dcc_mcp_photocraft.errors import AdapterError
from dcc_mcp_photocraft.paths import Workspace

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
PROJECT_EXTENSIONS = {".pcraft"}


@pytest.fixture
def workspace(tmp_path):
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    output_root.mkdir()
    return Workspace(input_root, output_root), input_root, output_root


def make_symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"This environment does not permit symlinks: {error}")


def image_payload(extension: str = "png", color: str = "red") -> bytes:
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), color=color).save(
        image_bytes, format="PNG" if extension == "png" else "JPEG"
    )
    return image_bytes.getvalue()


def test_input_resolution_and_output_validation_do_not_modify_files(workspace):
    policy, input_root, output_root = workspace
    source = input_root / "source.png"
    source.write_bytes(b"original input")
    (output_root / "nested").mkdir()

    assert policy.input_file("source.png", IMAGE_EXTENSIONS) == source.resolve()
    target = policy.output_file("nested/new.png", IMAGE_EXTENSIONS)

    assert target == output_root / "nested" / "new.png"
    assert not target.exists()
    assert source.read_bytes() == b"original input"


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "../outside.png",
        "folder/../../outside.png",
        "/outside.png",
        "C:/outside.png",
        "C:outside.png",
        "\\\\server\\share\\outside.png",
        "folder\\..\\outside.png",
        "folder//outside.png",
        "./outside.png",
        "outside.png:stream.png",
        "NUL.png",
        "folder/COM1.png",
        "folder. /outside.png",
        "folder./outside.png",
        "outside.png\x00.png",
    ],
)
@pytest.mark.parametrize("operation", ["input_file", "output_file"])
def test_untrusted_paths_fail_closed(workspace, relative, operation):
    policy, input_root, output_root = workspace
    outside = input_root.parent / "outside.png"
    outside.write_bytes(b"outside canary")
    original_entries = list(output_root.iterdir())

    with pytest.raises(AdapterError) as caught:
        getattr(policy, operation)(relative, IMAGE_EXTENSIONS)

    assert isinstance(caught.value.code, str)
    assert caught.value.code
    assert outside.read_bytes() == b"outside canary"
    assert list(output_root.iterdir()) == original_entries


def test_absolute_paths_inside_authorized_roots_are_still_rejected(workspace):
    policy, input_root, output_root = workspace
    source = input_root / "source.png"
    source.write_bytes(b"input")

    with pytest.raises(AdapterError):
        policy.input_file(str(source), IMAGE_EXTENSIONS)
    with pytest.raises(AdapterError):
        policy.output_file(str(output_root / "new.png"), IMAGE_EXTENSIONS)


@pytest.mark.parametrize("relative", ["missing.png", "folder.png"])
def test_input_requires_an_existing_regular_file(workspace, relative):
    policy, input_root, _ = workspace
    (input_root / "folder.png").mkdir()

    with pytest.raises(AdapterError):
        policy.input_file(relative, IMAGE_EXTENSIONS)


def test_input_cannot_read_from_output_root(workspace):
    policy, _, output_root = workspace
    (output_root / "export.png").write_bytes(b"output only")

    with pytest.raises(AdapterError):
        policy.input_file("export.png", IMAGE_EXTENSIONS)
    with pytest.raises(AdapterError):
        policy.input_file("../output/export.png", IMAGE_EXTENSIONS)


def test_file_extension_allowlist_is_enforced_for_both_roots(workspace):
    policy, input_root, _ = workspace
    (input_root / "script.py").write_text("raise RuntimeError('must not run')")

    with pytest.raises(AdapterError):
        policy.input_file("script.py", IMAGE_EXTENSIONS)
    with pytest.raises(AdapterError):
        policy.output_file("image.png.exe", IMAGE_EXTENSIONS)


def test_output_parent_must_already_exist(workspace):
    policy, _, output_root = workspace

    with pytest.raises(AdapterError):
        policy.output_file("missing/new.png", IMAGE_EXTENSIONS)

    assert not (output_root / "missing").exists()


@pytest.mark.parametrize("existing_kind", ["file", "directory"])
def test_output_never_overwrites_an_existing_target(workspace, existing_kind):
    policy, _, output_root = workspace
    target = output_root / "existing.png"
    if existing_kind == "file":
        target.write_bytes(b"preserve this output")
    else:
        target.mkdir()

    with pytest.raises(AdapterError):
        policy.output_file("existing.png", IMAGE_EXTENSIONS)

    if existing_kind == "file":
        assert target.read_bytes() == b"preserve this output"
    else:
        assert target.is_dir()


def test_output_rejects_a_dangling_symlink(workspace):
    policy, _, output_root = workspace
    missing_target = output_root.parent / "missing.png"
    link = output_root / "existing.png"
    make_symlink(link, missing_target)

    with pytest.raises(AdapterError):
        policy.output_file("existing.png", IMAGE_EXTENSIONS)

    assert link.is_symlink()
    assert not missing_target.exists()


@pytest.mark.parametrize("overlap", ["same", "output_inside_input", "input_inside_output"])
def test_input_and_output_roots_must_be_disjoint(tmp_path, overlap):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    input_root, output_root = {
        "same": (parent, parent),
        "output_inside_input": (parent, child),
        "input_inside_output": (child, parent),
    }[overlap]

    with pytest.raises(AdapterError):
        Workspace(input_root, output_root)


def test_root_aliases_cannot_hide_overlap(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    alias = tmp_path / "alias"
    make_symlink(alias, root, directory=True)

    with pytest.raises(AdapterError):
        Workspace(root, alias)


def test_symlinked_input_cannot_escape_read_root(workspace):
    policy, input_root, _ = workspace
    outside = input_root.parent / "outside"
    outside.mkdir()
    secret = outside / "secret.png"
    secret.write_bytes(b"outside secret")
    make_symlink(input_root / "link", outside, directory=True)

    with pytest.raises(AdapterError):
        policy.input_file("link/secret.png", IMAGE_EXTENSIONS)
    with pytest.raises(AdapterError):
        policy.stage_input("link/secret.png")

    assert secret.read_bytes() == b"outside secret"


def test_symlinked_output_parent_cannot_escape_write_root(workspace):
    policy, _, output_root = workspace
    outside = output_root.parent / "outside"
    outside.mkdir()
    make_symlink(output_root / "link", outside, directory=True)

    with pytest.raises(AdapterError):
        policy.output_file("link/new.png", IMAGE_EXTENSIONS)

    assert not (outside / "new.png").exists()


def test_reopen_requires_recorded_creation_in_the_current_session(workspace):
    policy, _, _ = workspace
    target = policy.output_file("created.pcraft", PROJECT_EXTENSIONS)
    target.write_bytes(b"created project")

    with pytest.raises(AdapterError):
        policy.reopen_file("created.pcraft")

    artifact = policy.mark_created("created.pcraft")

    assert artifact["sha256"] == hashlib.sha256(b"created project").hexdigest()
    assert policy.reopen_file("created.pcraft") == target.resolve()


def test_new_session_rejects_a_nonempty_output_root(workspace):
    _, input_root, output_root = workspace
    (output_root / "previous.pcraft").write_bytes(b"previous session output")

    with pytest.raises(AdapterError):
        Workspace(input_root, output_root)


def test_reopen_rejects_input_files_and_missing_outputs(workspace):
    policy, input_root, _ = workspace
    (input_root / "source.pcraft").write_bytes(b"original project")

    for relative in ("source.pcraft", "missing.pcraft", "../input/source.pcraft"):
        with pytest.raises(AdapterError):
            policy.reopen_file(relative)


@pytest.mark.parametrize("change", ["replace_contents", "remove"])
def test_reopen_rejects_output_changed_after_recording(workspace, change):
    policy, _, output_root = workspace
    target = output_root / "created.pcraft"
    target.write_bytes(b"approved project")
    policy.mark_created("created.pcraft")
    if change == "replace_contents":
        target.write_bytes(b"modified project")
    else:
        target.unlink()

    with pytest.raises(AdapterError):
        policy.reopen_file("created.pcraft")


def test_reopen_rejects_symlink_replacement_even_when_contents_match(workspace):
    policy, _, output_root = workspace
    target = output_root / "created.pcraft"
    target.write_bytes(b"same contents")
    policy.mark_created("created.pcraft")
    outside = output_root.parent / "outside.pcraft"
    outside.write_bytes(b"same contents")
    target.unlink()
    make_symlink(target, outside)

    with pytest.raises(AdapterError):
        policy.reopen_file("created.pcraft")


@pytest.mark.parametrize("extension", ["png", "jpg", "jpeg"])
def test_stage_input_copies_content_into_output_without_changing_source(workspace, extension):
    policy, input_root, output_root = workspace
    payload = image_payload(extension)
    source = input_root / f"source.{extension}"
    source.write_bytes(payload)

    relative = policy.stage_input(source.name)

    assert isinstance(relative, str)
    assert not Path(relative).is_absolute()
    assert relative.startswith(".imports/")
    staged = output_root / relative
    assert staged.resolve().is_relative_to(output_root.resolve())
    assert staged.read_bytes() == payload
    assert hashlib.sha256(payload).hexdigest() in staged.name
    assert source.read_bytes() == payload
    assert policy.stage_input(source.name) == relative


def test_staging_changed_input_preserves_previous_snapshot(workspace):
    policy, input_root, output_root = workspace
    first_payload = image_payload(color="red")
    second_payload = image_payload(color="blue")
    source = input_root / "source.png"
    source.write_bytes(first_payload)
    first = policy.stage_input("source.png")
    source.write_bytes(second_payload)

    second = policy.stage_input("source.png")

    assert first != second
    assert (output_root / first).read_bytes() == first_payload
    assert (output_root / second).read_bytes() == second_payload


def test_staging_does_not_overwrite_a_tampered_snapshot(workspace):
    policy, input_root, output_root = workspace
    payload = image_payload()
    source = input_root / "source.png"
    source.write_bytes(payload)
    relative = policy.stage_input("source.png")
    staged = output_root / relative
    staged.write_bytes(b"different bytes")

    with pytest.raises(AdapterError):
        policy.stage_input("source.png")

    assert staged.read_bytes() == b"different bytes"
    assert source.read_bytes() == payload


def test_staging_rejects_external_native_projects(workspace):
    policy, input_root, output_root = workspace
    source = input_root / "external.pcraft"
    source.write_bytes(b"untrusted native project")
    original_entries = list(output_root.rglob("*"))

    with pytest.raises(AdapterError):
        policy.stage_input("external.pcraft")

    assert source.read_bytes() == b"untrusted native project"
    assert list(output_root.rglob("*")) == original_entries


def test_staging_rejects_symlinked_snapshot_even_when_contents_match(workspace):
    policy, input_root, output_root = workspace
    payload = image_payload()
    source = input_root / "source.png"
    source.write_bytes(payload)
    staged = output_root / policy.stage_input("source.png")
    staged.unlink()
    make_symlink(staged, source)

    with pytest.raises(AdapterError):
        policy.stage_input("source.png")

    assert source.read_bytes() == payload
