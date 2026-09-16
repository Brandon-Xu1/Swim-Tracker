"""Inspect uploads and atomically import results, originals and profile links."""

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from sqlalchemy import text

from . import database as db
from .accounts import require_role
from .parser import inspect_cl2_text
from .profiles import link_rows
from .storage import imports, transaction

MAX_UPLOAD_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ImportOutcome:
    count: int
    filename: str
    duplicate: bool = False


def read_upload(filename, content):
    """Read bounded CL2 archives in memory; never extract paths to disk."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Uploads must be 8 MB or smaller.")
    if filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = [
                    item
                    for item in archive.infolist()
                    if not item.is_dir() and item.filename.lower().endswith(".cl2")
                ]
                if not entries or len(entries) > 20:
                    raise ValueError("A ZIP must contain between 1 and 20 CL2 files.")
                if sum(item.file_size for item in entries) > MAX_UPLOAD_BYTES:
                    raise ValueError("Uncompressed CL2 files must total at most 8 MB.")
                files = [
                    (PurePosixPath(item.filename).name, archive.read(item)) for item in entries
                ]
                if len({name for name, _ in files}) != len(files):
                    raise ValueError("CL2 filenames inside a ZIP must be unique.")
                return files
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise ValueError("Unable to read this ZIP archive.") from exc
    if not filename.lower().endswith(".cl2"):
        raise ValueError("Choose a CL2 file or a ZIP containing CL2 files.")
    return [(PurePosixPath(filename).name, content)]


def inspect_upload(filename, content):
    if len(content) > MAX_UPLOAD_BYTES or b"\x00" in content:
        raise ValueError("Invalid CL2 file or file larger than 8 MB.")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("cp1252", errors="replace")
    return inspect_cl2_text(decoded, filename)


def fingerprint(results):
    values = []
    for row in results:
        value = asdict(row) if hasattr(row, "__dataclass_fields__") else dict(row)
        values.append(
            {
                key: value[key]
                for key in (
                    "athlete_id",
                    "name",
                    "age",
                    "gender",
                    "event_id",
                    "time_seconds",
                    "course",
                    "meet_date",
                )
            }
        )
    return hashlib.sha256(
        json.dumps(
            sorted(values, key=lambda value: json.dumps(value, sort_keys=True)), sort_keys=True
        ).encode()
    ).hexdigest()


def import_meet(
    target,
    filename,
    content,
    *,
    team_id=0,
    account=None,
    public_admin=False,
    allow_partial=False,
    replace_existing=False,
):
    report = inspect_upload(filename, content)
    if not report.results:
        raise ValueError("No valid completed individual results were found.")
    if report.issues and not allow_partial:
        raise ValueError(
            "Review the import errors and explicitly accept a partial import before continuing."
        )
    digest = fingerprint(report.results)
    with transaction(target, f"team:{team_id}") as c:
        if team_id == 0:
            if not public_admin:
                raise PermissionError("Public meet data requires administrator access.")
        else:
            require_role(c, account, team_id)
        # Backfill fingerprints for previously imported meets without changing their results.
        legacy = (
            c.execute(
                text(
                    "SELECT DISTINCT source_file FROM results WHERE team_id=:team "
                    "AND source_file NOT IN (SELECT filename FROM imports WHERE team_id=:team)"
                ),
                {"team": team_id},
            )
            .scalars()
            .all()
        )
        for source in legacy:
            rows = (
                c.execute(
                    text("SELECT * FROM results WHERE team_id=:team AND source_file=:file"),
                    {"team": team_id, "file": source},
                )
                .mappings()
                .all()
            )
            c.execute(
                text(
                    "INSERT INTO imports (id, team_id, filename, fingerprint, uploaded_at) "
                    "VALUES (:id, :team, :file, :digest, :now) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "team": team_id,
                    "file": source,
                    "digest": fingerprint(rows),
                    "now": db._utc_now_iso(),
                },
            )
        duplicate = c.execute(
            text("SELECT filename FROM imports WHERE team_id=:team AND fingerprint=:digest"),
            {"team": team_id, "digest": digest},
        ).scalar()
        if duplicate and duplicate != filename:
            return ImportOutcome(len(report.results), duplicate, True)
        existing_id = c.execute(
            text("SELECT id FROM imports WHERE team_id=:team AND filename=:file"),
            {"team": team_id, "file": filename},
        ).scalar()
        # Identical legacy files may share a fingerprint, so one can remain without
        # import metadata. Its existing results still require replacement consent.
        if (existing_id or filename in legacy) and duplicate != filename and not replace_existing:
            raise ValueError(
                "A different version of this filename exists. Confirm replacement first."
            )
        params = {"team": team_id, "file": filename}
        c.execute(
            text(
                "DELETE FROM result_profiles WHERE result_id IN (SELECT id FROM results WHERE team_id=:team AND source_file=:file)"
            ),
            params,
        )
        c.execute(text("DELETE FROM results WHERE team_id=:team AND source_file=:file"), params)
        c.execute(
            db.results_table.insert(),
            [{"team_id": team_id, **asdict(row)} for row in report.results],
        )
        c.execute(text("DELETE FROM raw_files WHERE team_id=:team AND filename=:file"), params)
        c.execute(
            db.raw_files_table.insert().values(
                team_id=team_id, filename=filename, content=content, uploaded_at=db._utc_now_iso()
            )
        )
        c.execute(text("DELETE FROM imports WHERE team_id=:team AND filename=:file"), params)
        c.execute(
            imports.insert().values(
                id=existing_id or uuid.uuid4().hex,
                team_id=team_id,
                filename=filename,
                fingerprint=digest,
                uploaded_at=db._utc_now_iso(),
            )
        )
        rows = (
            c.execute(
                text("SELECT * FROM results WHERE team_id=:team AND source_file=:file"), params
            )
            .mappings()
            .all()
        )
        link_rows(c, team_id, rows)
    return ImportOutcome(len(report.results), filename)


def remove_meet(target, filename, *, team_id, account=None, public_admin=False):
    with transaction(target, f"team:{team_id}") as c:
        if team_id == 0:
            if not public_admin:
                raise PermissionError("Public meet data requires administrator access.")
        else:
            require_role(c, account, team_id)
        params = {"team": team_id, "file": filename}
        c.execute(
            text(
                "DELETE FROM result_profiles WHERE result_id IN (SELECT id FROM results WHERE team_id=:team AND source_file=:file)"
            ),
            params,
        )
        count = c.execute(
            text("DELETE FROM results WHERE team_id=:team AND source_file=:file"), params
        ).rowcount
        c.execute(text("DELETE FROM imports WHERE team_id=:team AND filename=:file"), params)
        c.execute(text("DELETE FROM raw_files WHERE team_id=:team AND filename=:file"), params)
        return count
