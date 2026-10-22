# -*- coding: utf-8 -*-
"""The cloud backup of oshinei.db (backend/cloud_backup.py, docs/CLOUD_SYNC.md), as seen from the Settings page.

    GET  /api/admin/cloud-backup/status   last run, result, restore test, next run, the copies in the bucket   (Management)
    POST /api/admin/cloud-backup/run      back up now; refused while one is running or the feature is off

Both are Management only (module system.admin, auth.MODULE_RULES, and checked again here like the other admin
endpoints). The POST also needs the write token, like every other admin write.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

import cloud_backup as _cb
from core import require_write_token
from .auth_api import _require_admin

router = APIRouter()


@router.get("/api/admin/cloud-backup/status")
def cloud_backup_status(request: Request):
    _require_admin(request)
    return _cb.get().view()


@router.post("/api/admin/cloud-backup/run", dependencies=[Depends(require_write_token)])
def cloud_backup_run(request: Request):
    _require_admin(request)
    if not _cb.enabled():
        raise HTTPException(status_code=409, detail="ยังไม่ได้เปิดการสำรองข้อมูลบนคลาวด์ (OSHINEI_CLOUD_BACKUP)")
    if not _cb.get().run_in_background():
        raise HTTPException(status_code=409, detail="กำลังสำรองข้อมูลอยู่")
    return {"ok": True, "started": True}
