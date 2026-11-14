"""Where uploaded files land, and the rules for accepting them.

Separated from the routes because the security-relevant decisions are
all here, and they are easier to review and test in one place than
scattered through endpoint bodies.

The threat being defended against is not exotic: a web endpoint that
accepts a file and writes it to disk using the client's own filename is
the textbook path-traversal bug (a name like "../../../.ssh/authorized_keys"
escapes the upload directory). Every filename arriving from a browser is
attacker-controlled, so none of it is ever used to build a path here --
we generate our own name and keep only a validated extension.
"""

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

# Mirrors what GeminiProvider can actually send to the model. Accepting
# anything wider would just produce a confusing failure deeper in.
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

# Portal screenshots and scanned deeds are comfortably under this. The
# limit exists so one request cannot fill the disk.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _safe_suffix(filename: str | None) -> str:
    """The file's extension, if we allow it. Never returns a path."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"unsupported file type {suffix or '(none)'}; "
                f"allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}"
            ),
        )
    return suffix


def save_upload(upload: UploadFile, check_dir: Path) -> Path:
    """Persist one uploaded file and return where it landed.

    The stored name is a generated UUID plus the validated extension --
    the client's filename is never used to build the path, only to read
    its extension. That keeps a crafted name like "../../etc/passwd"
    from escaping check_dir.
    """
    suffix = _safe_suffix(upload.filename)
    contents = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
        )
    if not contents:
        raise HTTPException(status_code=400, detail="file is empty")

    check_dir.mkdir(parents=True, exist_ok=True)
    destination = check_dir / f"{uuid.uuid4().hex}{suffix}"
    destination.write_bytes(contents)
    return destination


def check_dir_for(root: Path, check_id: str) -> Path:
    """The upload directory for one check.

    check_id reaches us from the URL, so it is attacker-controlled too.
    Resolving the candidate path and confirming it is still inside root
    rejects any id that tries to climb out with "..", without having to
    reason about every character that might appear in one.
    """
    candidate = (root / check_id).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise HTTPException(status_code=400, detail="invalid check id")
    return candidate
