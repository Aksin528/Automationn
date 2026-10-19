"""Fork-local: browser-friendly download link for a case's incident report PDF.

`GET /cases/{case_id}/report/pdf` (router.py) answers with JSON (`download_url`,
`file_name`), which is what the frontend wants but cannot be used as a plain link
(for example from a Grafana dashboard). The route below runs the same checks and
answers with a redirect to the short-lived pre-signed URL instead, so a click
downloads the PDF using the user's normal Tracecat login.

It lives in its own module so upstream changes to router.py cannot conflict with it;
router.py only includes `router` (see the "fork-local" block there).
"""

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from starlette.status import (
    HTTP_307_TEMPORARY_REDIRECT,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger

# No prefix here: router.py includes this into `cases_router`, which adds "/cases".
router = APIRouter()


@router.get("/{case_id}/report/pdf/redirect", include_in_schema=False)
@require_scope("case:read")
async def redirect_to_incident_report_pdf(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> RedirectResponse:
    """Redirect to a short-lived pre-signed URL of the case's stored report PDF."""
    from tracecat.storage import blob

    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_REPORTS
    key = f"reports/{case.short_id}.pdf"
    if not await blob.file_exists(key, bucket):
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="No report PDF has been generated for this case yet",
        )

    try:
        download_url = await blob.generate_presigned_download_url(
            key=key,
            bucket=bucket,
            override_content_type="application/pdf",
        )
    except Exception as exc:
        logger.error(
            "Failed to generate incident report download URL",
            case_id=case_id,
            bucket=bucket,
            key=key,
            error=str(exc),
        )
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate incident report download URL",
        ) from exc

    # The URL is only valid for a few seconds: never let a browser or proxy cache the redirect.
    return RedirectResponse(
        download_url,
        status_code=HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "no-store"},
    )
