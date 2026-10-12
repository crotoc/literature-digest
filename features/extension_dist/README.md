# features/extension_dist

浏览器扩展的动态打包与分发。

## 职责

- 扫描 `extension/` 源码目录
- 动态打包成 ZIP（无缓存，每次都生成）
- 返回可下载的 ZIP 字节流

## 接口

- `package_extension(project_root)` → ExtensionPackageDTO

## 约定

- 打包时排除：node_modules、.git、.dist、.* 文件
- 返回的 ZIP 包含完整 extension/ 目录结构
- 无版本检查——版本由 manifest.json 记录
