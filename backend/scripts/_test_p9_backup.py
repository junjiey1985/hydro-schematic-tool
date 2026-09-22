# -*- coding: utf-8 -*-
"""P9 验收测试：项目导出 / 导入 zip（备份与迁移闭环）。"""
import io
import json
import sys
import urllib.error
import urllib.request
import uuid
import zipfile

BASE = "http://127.0.0.1:8013"
OK = []


def chk(name, cond, extra=""):
    if cond:
        OK.append(name)
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name} {extra}")


def call(method, path, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw.decode("utf-8")), raw
            except Exception:
                return r.status, None, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300], None


def call_multipart(path, filename, file_bytes, fields=None, timeout=120):
    b = uuid.uuid4().hex
    parts = []
    for k, v in (fields or {}).items():
        parts.append(
            f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
        )
    parts.append(
        (
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{b}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={b}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def main():
    # [1] 选主项目（图层数最多）
    st, listing, _ = call("GET", "/api/projects")
    chk("项目列表可取", st == 200)
    projects = sorted(listing["projects"], key=lambda p: -p["layer_count"])
    main_pid = projects[0]["id"]
    main_name = projects[0]["name"]
    main_layers = projects[0]["layer_count"]
    print(f"  主项目: {main_pid} {main_name} ({main_layers} 图层)")

    # [2] 导出
    st, _, raw = call("GET", f"/api/projects/{main_pid}/export")
    chk("导出返回 200", st == 200)
    chk("导出为 zip", raw[:2] == b"PK")
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = set(zf.namelist())
    chk("zip 含 project.json", "project.json" in names)
    chk("zip 含 topology.json", "topology.json" in names)
    chk("zip 含 layers/", any(n.startswith("layers/") for n in names))
    size_kb = len(raw) / 1024
    print(f"  导出大小: {size_kb:.0f} KB, 条目数: {len(names)}")

    # [3] 导入（新名称）
    st, meta = call_multipart(
        "/api/projects/import", "backup.zip", raw, fields={"name": "_p9_导入测试项目"}
    )
    chk("导入返回 200", st == 200, str(meta)[:200])
    new_pid = meta.get("id", "")
    chk("导入生成新项目 id", new_pid.startswith("p_") and new_pid != main_pid)
    chk("导入名称正确", meta.get("name") == "_p9_导入测试项目")
    chk("导入后图层数一致", meta.get("layer_count") == main_layers, f"{meta.get('layer_count')} vs {main_layers}")

    # [4] 导出-导入往返一致性：条目集合 + topology/schematic 内容逐字节
    st2, _, raw2 = call("GET", f"/api/projects/{new_pid}/export")
    zf2 = zipfile.ZipFile(io.BytesIO(raw2))
    names2 = set(zf2.namelist())
    # project.json 允许不同（id/名称/时间戳被重写）
    names.discard("project.json")
    names2.discard("project.json")
    chk("往返条目集合一致", names == names2, f"仅一侧有: {list(names ^ names2)[:5]}")
    same = all(zf.read(n) == zf2.read(n) for n in names if n in zf.namelist() and n in zf2.namelist())
    chk("往返文件内容逐字节一致", same)

    # [5] 清理导入项目
    st3, _, _ = call("DELETE", f"/api/projects/{new_pid}")
    chk("清理导入项目", st3 == 200)

    # [6] 异常输入
    st4, msg4 = call_multipart("/api/projects/import", "bad.zip", b"this is not a zip")
    chk("非 zip 输入被拒 400", st4 == 400, str(msg4)[:120])
    bad_zf = io.BytesIO()
    with zipfile.ZipFile(bad_zf, "w") as z:
        z.writestr("readme.txt", "no manifest here")
    st5, msg5 = call_multipart("/api/projects/import", "bad2.zip", bad_zf.getvalue())
    chk("缺 project.json 被拒 400", st5 == 400, str(msg5)[:120])

    print(f"\n===== 结果: {len(OK)} PASS =====")
    return 0 if len(OK) >= 14 else 1


if __name__ == "__main__":
    sys.exit(main())
