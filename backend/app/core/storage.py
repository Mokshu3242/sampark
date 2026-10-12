"""Keep finished runs off the server's disk (free hosts wipe it).
Each run = one zip. Saved to MongoDB GridFS and Backblaze B2; either can be left unset."""
import gc
import io
import logging
import os
import shutil
import time
import zipfile
from urllib.parse import quote_plus

from app.core.config import settings

log = logging.getLogger("flood-watch.storage")
SKIP = {"s1_pair.tif", "osm_raw.json"}                          # big; the live app never reads them
PINNED = {"20261008T063513-021f3e", "20261008T230310-8ae6e4"}   # demo runs: never deleted
MONGO_LIMIT = 420 * 1024 * 1024                                 # stay under the 512 MB free cap
B2_PREFIX = "runs/"

_db = None
_fs = None
_s3 = None


# ---------- backends ----------

def _mongo_uri() -> str:
    uri = settings.mongo_uri
    if uri and settings.mongo_username:
        uri = uri.replace("MONGO_USERNAME", quote_plus(settings.mongo_username))
    if uri and settings.mongo_password:
        uri = uri.replace("MONGO_PASSWORD", quote_plus(settings.mongo_password))
    return uri


def mongo_db():
    """The 'floodwatch' database, or None if Mongo is not set up."""
    global _db
    uri = _mongo_uri()
    if _db is None and uri and "MONGO_PASSWORD" not in uri:
        from pymongo import MongoClient
        _db = MongoClient(uri, serverSelectionTimeoutMS=8000)["floodwatch"]
    return _db


def _mongo():
    global _fs
    if _fs is None and mongo_db() is not None:
        import gridfs
        _fs = gridfs.GridFS(mongo_db(), collection="runs")
    return _fs


def _b2():
    global _s3
    if _s3 is None and settings.b2_key_id and settings.b2_app_key and settings.b2_bucket and settings.b2_endpoint:
        import boto3
        ep = settings.b2_endpoint if settings.b2_endpoint.startswith("http") else f"https://{settings.b2_endpoint}"
        _s3 = boto3.client("s3", endpoint_url=ep,
                           aws_access_key_id=settings.b2_key_id, aws_secret_access_key=settings.b2_app_key)
    return _s3


def enabled() -> bool:
    return _mongo() is not None or _b2() is not None


# ---------- zip helpers ----------

def _zip_run(run_id: str) -> bytes:
    run_dir = settings.runs_dir / run_id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in run_dir.rglob("*"):
            if f.is_file() and f.name not in SKIP and not f.name.endswith(".part"):
                z.write(f, arcname=str(f.relative_to(run_dir)))
    return buf.getvalue()


def _unzip_run(run_id: str, data) -> None:
    """data is bytes or a path to a zip on disk. A path keeps memory low."""
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    src = data if isinstance(data, (str, os.PathLike)) else io.BytesIO(data)
    with zipfile.ZipFile(src) as z:
        z.extractall(run_dir)
        for info in z.infolist():                 # keep file times (road caches compare them)
            t = time.mktime(info.date_time + (0, 0, -1))
            os.utime(run_dir / info.filename, (t, t))


def _tmp_zip(run_id: str):
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    return settings.runs_dir / f".dl_{run_id}.zip"


def _download_to_disk(run_id: str):
    """Stream a run zip to a temp file in 1 MB pieces. Never holds the whole zip in memory.
    Returns (path, source) or (None, None)."""
    tmp = _tmp_zip(run_id)
    try:
        fs = _mongo()
        if fs is not None:
            g = fs.find_one({"filename": run_id})
            if g is not None:
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(g, f, 1024 * 1024)
                return tmp, "Mongo"
    except Exception as e:
        log.warning(f"Mongo read failed for {run_id}: {e}")
        tmp.unlink(missing_ok=True)
    try:
        s3 = _b2()
        if s3 is not None:
            with open(tmp, "wb") as f:
                s3.download_fileobj(settings.b2_bucket, f"{B2_PREFIX}{run_id}.zip", f)
            return tmp, "B2"
    except Exception as e:
        log.warning(f"B2 read failed for {run_id}: {e}")
        tmp.unlink(missing_ok=True)
    return None, None


def _restore_from_disk(run_id: str) -> bool:
    tmp, source = _download_to_disk(run_id)
    if tmp is None:
        return False
    try:
        size = tmp.stat().st_size
        _unzip_run(run_id, tmp)
        log.info(f"Restored {run_id} from {source} ({size / 1e6:.0f} MB)")
        return True
    finally:
        tmp.unlink(missing_ok=True)
        gc.collect()


def _on_disk(run_id: str) -> bool:
    return (settings.runs_dir / run_id / "request.json").exists()


# ---------- Mongo ----------

def _mongo_make_room(fs, incoming: int) -> None:
    files = sorted(fs.find(), key=lambda g: g.upload_date)     # oldest first
    used = sum(g.length for g in files)
    for g in files:
        if used + incoming <= MONGO_LIMIT:
            break
        if g.filename in PINNED:
            continue
        fs.delete(g._id)
        used -= g.length
        log.info(f"Mongo: removed old run {g.filename} to free space (still in B2 if enabled)")


def _mongo_put(run_id: str, data: bytes) -> None:
    fs = _mongo()
    if fs is None:
        return
    for old in fs.find({"filename": run_id}):
        fs.delete(old._id)
    _mongo_make_room(fs, len(data))
    fs.put(data, filename=run_id)
    log.info(f"Mongo: stored {run_id} ({len(data) / 1e6:.0f} MB)")


# ---------- B2 ----------

def _b2_put(run_id: str, data: bytes) -> None:
    s3 = _b2()
    if s3 is None:
        return
    s3.put_object(Bucket=settings.b2_bucket, Key=f"{B2_PREFIX}{run_id}.zip", Body=data)
    log.info(f"B2: stored {run_id} ({len(data) / 1e6:.0f} MB)")


def _b2_list() -> list[str]:
    s3 = _b2()
    if s3 is None:
        return []
    out, token = [], None
    while True:
        kw = {"Bucket": settings.b2_bucket, "Prefix": B2_PREFIX}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        out += [o["Key"][len(B2_PREFIX):-4] for o in r.get("Contents", []) if o["Key"].endswith(".zip")]
        if not r.get("IsTruncated"):
            return out
        token = r["NextContinuationToken"]


def _b2_get(run_id: str) -> bytes | None:
    s3 = _b2()
    if s3 is None:
        return None
    try:
        return s3.get_object(Bucket=settings.b2_bucket, Key=f"{B2_PREFIX}{run_id}.zip")["Body"].read()
    except Exception:
        return None


# ---------- public ----------

def upload_run(run_id: str) -> None:
    """Save a finished run to every store that is set up. One store failing does not stop the other."""
    if not enabled():
        return
    data = _zip_run(run_id)
    for name, put in (("Mongo", _mongo_put), ("B2", _b2_put)):
        try:
            put(run_id, data)
        except Exception as e:
            log.warning(f"{name}: could not store {run_id}: {e}")


def has_run(run_id: str) -> bool:
    """Is a finished zip for this run in any store?"""
    try:
        fs = _mongo()
        if fs is not None and fs.exists({"filename": run_id}):
            return True
    except Exception as e:
        log.warning(f"Mongo check failed: {e}")
    try:
        s3 = _b2()
        if s3 is not None:
            s3.head_object(Bucket=settings.b2_bucket, Key=f"{B2_PREFIX}{run_id}.zip")
            return True
    except Exception:
        pass
    return False


def restore_run(run_id: str) -> bool:
    """Pull one run from storage onto disk (overwrites what is there). Mongo first, then B2."""
    return _restore_from_disk(run_id)


def stored_runs() -> list[str]:
    """Run IDs in any store, without downloading them."""
    ids = []
    try:
        fs = _mongo()
        if fs is not None:
            ids += [g.filename for g in fs.find()]
    except Exception as e:
        log.warning(f"Mongo list failed: {e}")
    try:
        ids += _b2_list()
    except Exception as e:
        log.warning(f"B2 list failed: {e}")
    return list(dict.fromkeys(ids))            # keep order, drop duplicates


def restore_all() -> int:
    """On startup: bring back every stored run that is not on disk, one at a time,
    streamed through a temp file. Pinned demo runs come first."""
    from app.core.memtrim import trim
    ids = stored_runs()
    ids.sort(key=lambda r: (r not in PINNED, r))
    n = 0
    for run_id in ids:
        if _on_disk(run_id):
            continue
        try:
            if _restore_from_disk(run_id):
                n += 1
        except Exception as e:
            log.warning(f"Restore failed for {run_id}: {e}")
        trim(f"restore {run_id}")
    return n


def delete_run(run_id: str) -> dict:
    """Remove a run from Mongo and B2. Pinned demo runs are refused."""
    if run_id in PINNED:
        raise ValueError("Demo runs are pinned and cannot be deleted")
    out = {"mongo": False, "b2": False}
    try:
        fs = _mongo()
        if fs is not None:
            for f in fs.find({"filename": {"$regex": f"^{run_id}"}}):
                fs.delete(f._id)
                out["mongo"] = True
    except Exception as e:
        log.warning(f"Mongo delete failed for {run_id}: {e}")
    try:
        s3 = _b2()
        if s3 is not None:
            resp = s3.list_objects_v2(Bucket=settings.b2_bucket, Prefix=f"{B2_PREFIX}{run_id}")
            for obj in resp.get("Contents", []):
                s3.delete_object(Bucket=settings.b2_bucket, Key=obj["Key"])
                out["b2"] = True
    except Exception as e:
        log.warning(f"B2 delete failed for {run_id}: {e}")
    return out