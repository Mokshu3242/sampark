"""Keep finished runs off the server's disk (free hosts wipe it).
Each run = one zip. Saved to MongoDB GridFS and Backblaze B2; either can be left unset."""
import io
import logging
import os
import time
import zipfile
from urllib.parse import quote_plus

from app.core.config import settings

log = logging.getLogger("flood-watch.storage")
SKIP = {"s1_pair.tif", "osm_raw.json"}                          # big; the live app never reads them
PINNED = {"20261008T063513-021f3e", "20261008T230310-8ae6e4"}   # demo runs: never deleted
MONGO_LIMIT = 420 * 1024 * 1024                                 # stay under the 512 MB free cap
B2_PREFIX = "runs/"

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


def _mongo():
    global _fs
    uri = _mongo_uri()
    if _fs is None and uri and "MONGO_PASSWORD" not in uri:
        import gridfs
        from pymongo import MongoClient
        db = MongoClient(uri, serverSelectionTimeoutMS=8000)["floodwatch"]
        _fs = gridfs.GridFS(db, collection="runs")
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
            if f.is_file() and f.name not in SKIP:
                z.write(f, arcname=str(f.relative_to(run_dir)))
    return buf.getvalue()


def _unzip_run(run_id: str, data: bytes) -> None:
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(run_dir)
        for info in z.infolist():                 # keep original file times (image cache keys on them)
            t = time.mktime(info.date_time + (0, 0, -1))
            os.utime(run_dir / info.filename, (t, t))


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


def restore_all() -> int:
    """On startup: bring back every stored run that is not on disk. Mongo first, then B2."""
    n = 0
    try:
        fs = _mongo()
        if fs is not None:
            for g in fs.find():
                if not _on_disk(g.filename):
                    _unzip_run(g.filename, g.read())
                    n += 1
                    log.info(f"Restored {g.filename} from Mongo")
    except Exception as e:
        log.warning(f"Mongo restore failed: {e}")
    try:
        s3 = _b2()
        for run_id in _b2_list():
            if not _on_disk(run_id):
                obj = s3.get_object(Bucket=settings.b2_bucket, Key=f"{B2_PREFIX}{run_id}.zip")
                _unzip_run(run_id, obj["Body"].read())
                n += 1
                log.info(f"Restored {run_id} from B2")
    except Exception as e:
        log.warning(f"B2 restore failed: {e}")
    return n

def delete_run(run_id: str) -> dict:
    """Remove a run from Mongo and B2. Pinned demo runs are refused."""
    if run_id in PINNED:
        raise ValueError("Demo runs are pinned and cannot be deleted")
    out = {"mongo": False, "b2": False}

    if settings.mongo_uri:
        try:
            import gridfs
            from pymongo import MongoClient
            client = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=8000)
            fs = gridfs.GridFS(client["floodwatch"], collection="runs")
            for f in fs.find({"filename": {"$regex": f"^{run_id}"}}):
                fs.delete(f._id)
                out["mongo"] = True
        except Exception as e:
            log.warning(f"Mongo delete failed for {run_id}: {e}")

    if settings.b2_bucket:
        try:
            import boto3
            ep = settings.b2_endpoint
            if not ep.startswith("http"):
                ep = "https://" + ep
            s3 = boto3.client("s3", endpoint_url=ep,
                              aws_access_key_id=settings.b2_key_id,
                              aws_secret_access_key=settings.b2_app_key)
            resp = s3.list_objects_v2(Bucket=settings.b2_bucket, Prefix=f"runs/{run_id}")
            for obj in resp.get("Contents", []):
                s3.delete_object(Bucket=settings.b2_bucket, Key=obj["Key"])
                out["b2"] = True
        except Exception as e:
            log.warning(f"B2 delete failed for {run_id}: {e}")
    return out