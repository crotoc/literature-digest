from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.shell.registry import NavItem
from app.shell.templating import templates

router = APIRouter()
nav = NavItem(key="health", label="状态", path="/healthz", icon="heart", order=900)


@router.get("/healthz", response_class=HTMLResponse)
def healthz(request: Request):
    return templates.TemplateResponse(request, "health/index.html", {"active_nav": "health"})


@router.get("/healthz.json")
def healthz_json():
    return {"status": "ok"}
