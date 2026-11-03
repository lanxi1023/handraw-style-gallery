"""Bounded deletion of expired generated images; preserve records and uploads."""
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse

RETENTION_DAYS=30
RETENTION_SECONDS=RETENTION_DAYS*86400
POOL_IMAGE=re.compile(r'^[a-f0-9]{64}\.(?:png|jpg|webp)$')
POOL_IMAGE_IN_TEXT=re.compile(r'(?<![a-f0-9])([a-f0-9]{64}\.(?:png|jpg|webp))(?![a-z])')


def expires_at(row):
    stamp=float(row.get('finished_at') or 0)
    if stamp<=0:stamp=float(row.get('created_at') or 0)
    return stamp+RETENTION_SECONDS if stamp>0 else None


def is_expired(row,now=None):
    expiry=expires_at(row)
    return row.get('state')=='completed' and (row.get('expired_at') is not None or (expiry is not None and expiry<=(time.time() if now is None else now)))


def pool_filename(url):
    parsed=urlparse(url or '')
    if parsed.hostname=='127.0.0.1' and parsed.port==8000 and parsed.path.startswith('/api/pool/images/'):
        name=parsed.path.removeprefix('/api/pool/images/')
        if POOL_IMAGE.fullmatch(name):return name
    return None


def remove_owned_file(directory,name,pattern):
    if not pattern.fullmatch(name):return 0
    root=Path(directory).resolve();path=root/name
    if path.resolve().parent!=root:raise ValueError('Image path is outside its cache directory')
    if not path.is_file():return 0
    size=path.stat().st_size
    path.unlink()
    return size


def timestamp(value):
    try:
        if isinstance(value,(int,float)):return float(value) if value>0 else None
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
        stamp=parsed.timestamp()
        return stamp if stamp>0 else None
    except (TypeError,ValueError):return None


def pool_image_ages(pool_db,cutoff):
    """Keep shared files used by recent/unfinished tasks, including non-studio jobs."""
    if not Path(pool_db).is_file():return None
    try:
        with closing(sqlite3.connect('file:'+Path(pool_db).resolve().as_posix()+'?mode=ro',uri=True,timeout=10)) as db:
            rows=db.execute('SELECT status,completed_at,created_at,result_json,image_path FROM tasks')
            protected=set();expired=set()
            for state,finished,created,result,image_path in rows:
                finished=timestamp(finished or created)
                if state not in ('completed','failed') or finished is None or finished>cutoff:
                    protected.update(POOL_IMAGE_IN_TEXT.findall(str(result or '')+' '+str(image_path or '')))
                else:expired.update(POOL_IMAGE_IN_TEXT.findall(str(result or '')+' '+str(image_path or '')))
            return protected,expired
    except sqlite3.Error:return None


def cleanup(db_path,data_dir,pool_dir,pool_db,now=None):
    now=time.time() if now is None else now
    cutoff=now-RETENTION_SECONDS
    report={'checked_at':now,'retention_days':RETENTION_DAYS,'newly_expired':0,'studio_files_removed':0,'pool_files_removed':0,'bytes_removed':0,'errors':0}
    with closing(sqlite3.connect(db_path,timeout=15)) as db:
        db.row_factory=sqlite3.Row
        cursor=db.execute("UPDATE jobs SET expired_at=? WHERE state='completed' AND expired_at IS NULL AND (CASE WHEN finished_at>0 THEN finished_at ELSE created_at END)>0 AND (CASE WHEN finished_at>0 THEN finished_at ELSE created_at END)<=?",(now,cutoff))
        report['newly_expired']=cursor.rowcount
        db.commit()
        expired=[dict(x) for x in db.execute("SELECT id,image_url FROM jobs WHERE state='completed' AND expired_at IS NOT NULL")]
        protected={pool_filename(x[0]) for x in db.execute("SELECT image_url FROM jobs WHERE expired_at IS NULL")}
    pool_ages=pool_image_ages(pool_db,cutoff)
    report['pool_metadata_available']=pool_ages is not None
    candidates=set()
    if pool_ages is not None:
        pool_protected,candidates=pool_ages
        protected.update(pool_protected)
    for row in expired:
        try:
            path=Path(data_dir)/'images'/row['id']
            exists=path.is_file()
            removed=remove_owned_file(Path(data_dir)/'images',row['id'],re.compile(r'^[a-f0-9]{32}$'))
            if exists and not path.exists():report['studio_files_removed']+=1
            report['bytes_removed']+=removed
            name=pool_filename(row['image_url'])
            if name:candidates.add(name)
        except (OSError,ValueError):report['errors']+=1
    # Includes images from the original local pool UI, while preserving files
    # shared by any recent or unfinished task. Never delete unregistered files.
    if pool_ages is not None:
        for name in candidates-protected:
            try:
                path=Path(pool_dir)/name;exists=path.is_file()
                report['bytes_removed']+=remove_owned_file(pool_dir,name,POOL_IMAGE)
                if exists and not path.exists():report['pool_files_removed']+=1
            except (OSError,ValueError):report['errors']+=1
    Path(data_dir).mkdir(parents=True,exist_ok=True)
    (Path(data_dir)/'retention-status.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
