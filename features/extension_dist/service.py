"""features/extension_dist 服务层：动态打包扩展源码。"""

import os
from io import BytesIO
from pathlib import Path

from caps.archive import write_zip_to, entry
from features.extension_dist.contract import ExtensionPackageDTO, ExtensionPackageError


def package_extension(project_root: str = None) -> ExtensionPackageDTO:
    """
    动态打包 extension/ 源码成 ZIP。
    
    扫描 extension/ 目录，打包所有文件（除了 node_modules、.git 等）。
    
    Args:
        project_root: 项目根目录（默认为环境变量或当前目录）
    
    Returns:
        ExtensionPackageDTO(filename, content)
    """
    if project_root is None:
        project_root = os.getenv("PROJECT_ROOT", ".")

    extension_path = Path(project_root) / "extension"
    if not extension_path.exists():
        raise ExtensionPackageError(f"extension directory not found at {extension_path}")

    # 收集要打包的文件条目
    entries_list = []
    for root, dirs, filenames in os.walk(extension_path):
        # 跳过某些目录
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".dist")]

        for filename in filenames:
            # 跳过某些文件
            if filename.startswith("."):
                continue
            filepath = Path(root) / filename
            arcname = filepath.relative_to(extension_path)
            
            # 读文件内容
            with open(filepath, "rb") as f:
                data = f.read()
            
            # 创建 ZIP 条目
            entries_list.append(entry(str(arcname), data))

    if not entries_list:
        raise ExtensionPackageError("No files found to package")

    # 用 caps/archive 打包
    output = BytesIO()
    write_zip_to(output, entries_list)
    zip_bytes = output.getvalue()

    return ExtensionPackageDTO(
        filename="literature-digest-ext.zip",
        content=zip_bytes,
        content_type="application/zip",
    )
