"""启动后端服务（前端已构建时单端口同时提供页面与接口）。

用法::

    python run.py                  # 默认 0.0.0.0:8000
    python run.py --port 9000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    import uvicorn

    from app.config import FRONTEND_DIST

    ap = argparse.ArgumentParser(description="水系概化图工具")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="开发模式，代码变更自动重启")
    args = ap.parse_args()

    built = (FRONTEND_DIST / "index.html").exists()
    print("=" * 62)
    print("  水系概化图工具")
    print(f"  前端静态资源：{'已构建' if built else '未构建（请先执行 frontend/npm run build）'}")
    print(f"  访问地址：http://127.0.0.1:{args.port}/")
    print(f"  接口文档：http://127.0.0.1:{args.port}/docs")
    print("=" * 62)
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
