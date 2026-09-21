"""全局配置与路径定义。"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 路径
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # 项目根目录
DATA_DIR = Path(os.environ.get("HYDRO_DATA_DIR", BASE_DIR / "data"))
PROJECTS_DIR = DATA_DIR / "projects"
SAMPLES_DIR = DATA_DIR / "samples"
UPLOAD_DIR = DATA_DIR / "_uploads"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

for _p in (DATA_DIR, PROJECTS_DIR, SAMPLES_DIR, UPLOAD_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 坐标系
WGS84 = "EPSG:4326"
WEB_MERCATOR = "EPSG:3857"

# ---------------------------------------------------------------- 图层类型
# river    河流（线）      hydro_station 水文站（点）
# rain_station 雨量站（点）  lake 湖泊/水库（面）    boundary 流域边界（面）
# subbasin 子流域（面）    control_point 控制断面（点，手工指定子流域出口）
# other    其他
LAYER_TYPES = {
    "river": {"label": "河流水系", "geometry": "LineString"},
    "hydro_station": {"label": "水文站", "geometry": "Point"},
    "rain_station": {"label": "雨量站", "geometry": "Point"},
    "lake": {"label": "湖泊/水库", "geometry": "Polygon"},
    "boundary": {"label": "流域边界", "geometry": "Polygon"},
    "subbasin": {"label": "子流域", "geometry": "Polygon"},
    "control_point": {"label": "控制断面", "geometry": "Point"},
    "other": {"label": "其他", "geometry": "Any"},
}

# ---------------------------------------------------------------- 默认参数
DEFAULT_OPTIONS = {
    "topology": {
        # 节点合并容差（米）：小于该距离的端点/交点视为同一节点
        "snap_tolerance_m": 30.0,
        # 是否合并“假节点”（仅有两条河段首尾相接、无支流汇入的节点）
        "dissolve_pseudo_nodes": True,
        # 流向判定方式 auto | dem | attribute | digitized
        "flow_direction": "auto",
        # 站点挂接最大容许距离（米），超出则标记为未挂接
        "station_snap_max_m": 800.0,
        # 是否在站点处打断河段（水文站通常需要成为控制断面）
        "split_at_hydro_station": True,
    },
    "dem": {
        # 提取河网的汇流累积阈值（栅格数）
        "accum_threshold": 600,
        # 填洼时的抬升梯度
        "fill_epsilon": 1e-4,
        # 河网抽稀容差（米）
        "simplify_m": 120.0,
        # 矢量化后是否做拐角圆滑
        "smooth": True,
    },
    "schematic": {
        # 布局方向 vertical（上游在上） | horizontal（上游在左）
        "orientation": "vertical",
        # 相邻层级间距（像素）
        "level_gap": 110.0,
        # 同层最小叶节点间距（像素）
        "leaf_gap": 64.0,
        # 站点符号相对河段的偏移（像素）
        "station_offset": 34.0,
        # 是否绘制汇流节点符号
        "show_nodes": True,
    },
    "subbasin": {
        # 划分依据 station（水文站为控制断面） | junction（按汇流节点自动分区） | manual（手工控制点）
        "mode": "station",
        # 最小子流域面积（km²），自动分区时过滤过小单元
        "min_area_km2": 100,
        # 控制断面吸附到河道的最大距离（米）
        "snap_max_m": 10000,
        # 河道判定阈值（汇流格数），留空则用项目河网提取阈值
        "channel_accum_threshold": None,
        # 是否把流域出口也作为一个控制断面
        "include_outlet": True,
        # 子流域名前缀
        "name_prefix": "子流域",
    },
}
