from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import current_account, db
from app.shell.registry import NavItem
from app.shell.templating import templates
from domain.accounts import AccountDTO
from domain.reports import get_report, list_deliveries_for_report, list_report_items, list_reports

router = APIRouter()
nav = NavItem(key="reports", label="报告", path="/reports", icon="report", order=40)


def _owned_report(session, account_id: int, report_id: int):
    report = get_report(session, report_id)
    if report.account_id != account_id:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@router.get("/reports", response_class=HTMLResponse)
def reports_view(
    request: Request, account: AccountDTO | None = Depends(current_account), session=Depends(db)
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "reports/index.html",
        {
            "active_nav": "reports",
            "account": account,
            "reports": list_reports(session, account_id=account.id),
        },
    )


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_detail(
    report_id: int,
    request: Request,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    report = _owned_report(session, account.id, report_id)
    return templates.TemplateResponse(
        request,
        "reports/detail.html",
        {
            "active_nav": "reports",
            "account": account,
            "report": report,
            "items": list_report_items(session, report_id=report.id),
            "deliveries": list_deliveries_for_report(session, report.id),
        },
    )
