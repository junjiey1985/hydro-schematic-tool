"""水系概化图工具 —— FastAPI 服务入口。

单端口同时提供 REST 接口与前端静态资源，便于内网/云端一体化部署。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .config import FRONTEND_DIST
from .routers import analysis, dem_api, layers, projects, subbasins, timeseries

app = FastAPI(
    title="水系概化图工具",
    description="水系/站点 SHP 导入、DEM 实时生成河网、拓扑关系构建与水系概化图编辑",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(timeseries.router)
app.include_router(layers.router)
app.include_router(analysis.router)
app.include_router(dem_api.router)
app.include_router(subbasins.router)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "hydro-schematic",
        "version": app.version,
        "frontend_built": (FRONTEND_DIST / "index.html").exists(),
    }


# ---------------------------------------------------------------- 前端静态资源
def _mount_frontend() -> None:
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        @app.get("/")
        def _no_frontend():
            return JSONResponse(
                {
                    "ok": False,
                    "message": "前端尚未构建。请在 frontend 目录执行 npm install && npm run build，"
                    "或使用 npm run dev 启动开发服务器（默认 5173 端口）。",
                    "api_docs": "/docs",
                }
            )

        return

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if full_path:
            candidate = (FRONTEND_DIST / full_path).resolve()
            try:
                candidate.relative_to(FRONTEND_DIST.resolve())
            except ValueError:
                return FileResponse(index)
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)


_mount_frontend()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
