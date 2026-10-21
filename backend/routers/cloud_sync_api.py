# -*- coding: utf-8 -*-
"""The cloud pull of Easy POS (backend/cloud_sync.py, docs/CLOUD_SYNC.md), as seen from the Settings page.

    GET  /api/admin/cloud-sync/status   last run, result, next run, whether the scheduler is on   (Management)
    POST /api/admin/cloud-sync/run      start a pull now; refused while one is running or the feature is off

Both are Management only (module system.admin, auth.MODULE_RULES, and checked again here like the other
admin endpoints). The POST also needs the write token, like every other admin write.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import cloud_sync as _cs
from core import require_write_token
from .auth_api import _require_admin

router = APIRouter()


class RunBody(BaseModel):
    full: bool = False        # True = the full rebuild (the 06:00 job); default is the 45-day pull


@router.get("/api/admin/cloud-sync/status")
def cloud_sync_status(request: Request):
    _require_admin(request)
    return _cs.get().view()


@router.post("/api/admin/cloud-sync/run", dependencies=[Depends(require_write_token)])
def cloud_sync_run(request: Request, body: Optional[RunBody] = None):
    _require_admin(request)
    if not _cs.enabled():
        raise HTTPException(status_code=409, detail="ยังไม่ได้เปิดการดึงข้อมูลบนคลาวด์ (OSHINEI_CLOUD_SYNC)")
    c = _cs.get()
    if not c.run_in_background(full=bool(body and body.full)):
        raise HTTPException(status_code=409, detail="กำลังดึงข้อมูลอยู่")
    return {"ok": True, "started": True, "kind": "full" if (body and body.full) else "incremental"}
