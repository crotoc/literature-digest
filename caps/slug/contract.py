"""caps/slug 的对外表面。外部只许 import 这里的东西。

两个纯函数，无状态、无 IO、无表。
"""

from caps.slug.service import normalize, slugify

__all__ = ["normalize", "slugify"]
