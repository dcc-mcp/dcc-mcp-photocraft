"""PhotoCraft workspace policy, independent of its stronger native path checks."""

import hashlib
import re
from pathlib import Path

from .errors import AdapterError

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 4096 * 4096
INPUT_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _relative(value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise AdapterError("invalid_path")
    # Windows device names, ADS, trailing aliases and alternate separators are
    # refused on every platform so a manifest has the same meaning everywhere.
    if "\\" in value or ":" in value or value.startswith("/"):
        raise AdapterError("invalid_path")
    parts = value.split("/")
    for part in parts:
        if (
            part in {"", ".", ".."}
            or part.startswith(".")
            or part.endswith((" ", "."))
            or re.search(r'[<>"|?*\x00-\x1f]', part)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        ):
            raise AdapterError("invalid_path")
    return Path(*parts)


def _under(root: Path, rel: Path) -> Path:
    candidate = root / rel
    resolved = candidate.resolve()
    if root not in resolved.parents or candidate.is_symlink():
        raise AdapterError("path_outside_workspace")
    # Reject links even when they point back inside the grant. This avoids
    # ambiguity in manifests and symlink aliases to existing outputs.
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise AdapterError("path_outside_workspace")
    return candidate


class Workspace:
    def __init__(self, input_root: Path, output_root: Path):
        self.input_root = Path(input_root).resolve(strict=True)
        self.output_root = Path(output_root).resolve()
        if not self.input_root.is_dir():
            raise AdapterError("invalid_workspace")
        if (
            self.input_root == self.output_root
            or self.input_root in self.output_root.parents
            or self.output_root in self.input_root.parents
        ):
            raise AdapterError("overlapping_roots")
        self.output_root.mkdir(parents=True, exist_ok=True)
        if not self.output_root.is_dir() or any(self.output_root.iterdir()):
            raise AdapterError("output_root_not_empty")
        self.import_root = self.output_root / ".imports"
        self.import_root.mkdir()
        self._created: dict[str, dict] = {}
        self.imports: list[dict] = []

    def input_file(self, relative: str, extensions=INPUT_EXTENSIONS) -> Path:
        path = _under(self.input_root, _relative(relative))
        if path.suffix.lower() not in extensions or not path.is_file():
            raise AdapterError("invalid_input")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise AdapterError("resource_limit")
        return path

    def output_file(self, relative: str, extensions) -> Path:
        path = _under(self.output_root, _relative(relative))
        if path.suffix.lower() not in extensions or not path.parent.is_dir():
            raise AdapterError("invalid_output")
        if path.exists():
            raise AdapterError("output_exists")
        return path

    def stage_input(self, relative: str) -> str:
        source = self.input_file(relative)
        data = source.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise AdapterError("resource_limit")
        if source.suffix.lower() != ".pcraft":
            from io import BytesIO

            from PIL import Image

            try:
                with Image.open(BytesIO(data)) as img:
                    if img.width * img.height > MAX_PIXELS or max(img.size) > 4096:
                        raise AdapterError("resource_limit")
                    expected = "PNG" if source.suffix.lower() == ".png" else "JPEG"
                    if (
                        img.format != expected
                        or getattr(img, "n_frames", 1) != 1
                        or img.mode not in {"RGB", "RGBA", "L", "LA", "P"}
                    ):
                        raise AdapterError("unsupported_image_format")
                    dimensions = [img.width, img.height]
                    img.verify()
            except AdapterError:
                raise
            except Exception:
                raise AdapterError("invalid_image") from None
        digest = hashlib.sha256(data).hexdigest()
        staged = self.import_root / (digest + source.suffix.lower())
        if staged.is_symlink() or (hasattr(staged, "is_junction") and staged.is_junction()):
            raise AdapterError("artifact_changed")
        if staged.exists():
            if not staged.is_file() or staged.stat().st_size != len(data):
                raise AdapterError("artifact_changed")
            if hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
                raise AdapterError("artifact_changed")
        else:
            with staged.open("xb") as stream:
                stream.write(data)
        self.imports.append(
            {"path": relative, "sha256": digest, "bytes": len(data), "dimensions": dimensions}
        )
        return staged.relative_to(self.output_root).as_posix()

    def mark_created(self, relative: str) -> dict:
        path = _under(self.output_root, _relative(relative))
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise AdapterError("artifact_invalid", indeterminate=True)
        data = path.read_bytes()
        artifact = {
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        self._created[relative] = artifact
        return dict(artifact)

    def reopen_file(self, relative: str) -> Path:
        path = _under(self.output_root, _relative(relative))
        record = self._created.get(relative)
        if record is None or path.suffix.lower() != ".pcraft" or not path.is_file():
            raise AdapterError("untracked_output")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise AdapterError("resource_limit")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise AdapterError("artifact_changed")
        return path

    def manifest(self) -> dict:
        return {"inputs": list(self.imports), "outputs": list(self._created.values())}
