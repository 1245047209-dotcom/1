# Nexus GitHub 在线更新发布

更新页面：https://github.com/1245047209-dotcom/1/releases

客户端设置“检查更新”调用公开 GitHub Releases API，不需要单独维护服务器。

## 后续发布新版本

1. 修改程序版本号，打包新安装程序，例如 `Nexus-v60-Windows-x64-Setup.exe`。
2. 生成 `Nexus-v60-SHA256.txt`，文件中记录安装程序的 SHA-256 和完整文件名。
3. 在仓库 Releases 新建正式版本，标签填 `v60`，上传 EXE 和校验文件，填写更新说明并发布。

安装 v59 及之后版本的用户会在后台定时检查时收到提示，或通过“设置 → 检查更新”立即获取。
安装前需用户确认。v58.1 及更早版本需要手动更新一次。

## 首次发布工作流

附带的 `.github/workflows/publish-release.yml` 验证 `release-input/release.json` 中的构建哈希，
再创建草稿 Release、上传完整附件，最后发布为正式版本。
发布令牌使用 GitHub Actions 自动生成的 `GITHUB_TOKEN`，不会进入软件或安装包。
不覆盖已经发布的同版本；需要更新时使用新的版本标签。

首次发布的安装包作为构建输入保存在 GitHub 仓库；后续也可以直接使用 Releases 网页上传，无需将安装包再次提交到仓库。
