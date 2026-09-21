"""生成样例水系数据。

用法::

    python -m app.core.synth            # 输出到 data/samples
"""
from pathlib import Path

from ..config import SAMPLES_DIR
from .synth import generate_samples

if __name__ == "__main__":
    generate_samples(Path(SAMPLES_DIR))
