# -*- coding: utf-8 -*-
"""Every cloud job in one list (backend/cloud_jobs.py, docs/CLOUD_SYNC.md "งานอื่นบน cloud"), as seen from the Settings page.

    GET  /api/admin/cloud-jobs/status   sync, backup, feedback, invisible, watchdog: on/off, times, next run, last result   (Management)
    POST /api/admin/cloud-jobs/run      {"job": "feedback" | "invisible" | "watchdog"} - run it now; refused while it runs or is off

The sync and the backup keep their own pages and buttons (cloud-sync / cloud-backup). Both calls are Management only (module
system.admin, auth.MODULE_RULES, checked again here); the POST also needs the write token, like every other admin write.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import cloud_jobs as _cj
from core import require_write_token
from .auth_api import _require_admin

router = APIRouter()


class RunBody(BaseModel):
    job: str


@router.get("/api/admin/cloud-jobs/status")
def cloud_jobs_status(request: Request):
    _require_admin(request)
    return _cj.overview()


@router.post("/api/admin/cloud-jobs/run", dependencies=[Depends(require_write_token)])
def cloud_jobs_run(request: Request, body: RunBody):
    _require_admin(request)
    if body.job not in _cj.SPECS:
        raise HTTPException(status_code=400, detail="งานนี้สั่งรันที่หน้านี้ไม่ได้ (ใช้ได้: feedback, invisible, watchdog)")
    if not _cj.enabled_job(body.job):
        raise HTTPException(status_code=409, detail=f"ยังไม่ได้เปิดงาน{_cj.LABEL_TH[body.job]}บนคลาวด์ ({_cj.SPECS[body.job]['flag']})")
    if not _cj.get(body.job).run_in_background():
        raise HTTPException(status_code=409, detail=f"งาน{_cj.LABEL_TH[body.job]}กำลังทำอยู่")
    return {"ok": True, "started": True, "job": body.job}
