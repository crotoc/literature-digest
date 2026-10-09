"""`title_year_key`：works 的去重指纹。

"怎么判断两条文献记录大概率是同一篇"是 works 的领域知识，不是通用字符串
处理，所以没有并入 `caps/slug`——这里只复用 `caps/slug.slugify` 做字符层
的规范化（大小写折叠、NFKC、去标点），自己只管"标题 + 年份怎么拼成一个
键"这一条业务规则。

这是一个**粗筛**指纹，不是精确判重：两条标题几乎一样但年份差一年的记录
故意不会落在同一个键上（宁可漏判交给人工在 `duplicate_candidates` 里确认，
也不自动合并两条可能是不同版本/不同论文的记录）。见
`domain/works/README.md`「刻意裁剪的范围」。
"""

from caps.slug import slugify


def compute_title_year_key(title: str | None, year: int | None) -> str:
    """标题规范化 + 年份拼接。

    标题和年份至少要有一个——两者都没有的记录没有任何可比较的信息，
    调用方应该在调用前就跳过它（不参与去重），而不是指望这里兜底出一个
    没有意义的键。

    Raises:
        ValueError: `title` 和 `year` 同时为空。
    """
    if not (title and title.strip()) and year is None:
        raise ValueError("title 和 year 不能同时为空")

    title_part = slugify(title, separator="") if title and title.strip() else ""
    year_part = str(year) if year is not None else ""
    return f"{title_part}_{year_part}"
