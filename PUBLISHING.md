# Nexus 版本发布

工作流 `.github/workflows/publish-release.yml` 在 Windows runner 内构建和验证安装包。

1. 修改 `app/` 下的源码及测试，把 `app/VERSION` 更新到新的版本。
2. 更新 `release-input/release.json` 中的版本与附件名称，以及 `release-input/notes.md` 中的发布说明。
3. 保留或核对固定的基线安装包和编译器 SHA-256；新增的图片、图标等资源提交到 `app/assets/`。
4. 提交到 main 后，工作流验证下载、重建干净的运行环境和产品图库、覆盖当前源码，再运行测试、GUI 检查和实际安装检查。
5. 生成安装包、完整源码 ZIP 和校验文件，先上传完整草稿 Release，最后发布为正式版并标记 latest。已有同版本会拒绝覆盖。

基线安装包只用于提供经校验的公开运行时及旧资源，用户设备列表、更新偏好、日志、进程文件和电量历史不进入发布包。安装检查会验证覆盖安装保留这些文件。

完整源码 ZIP 不包含 Python 运行时，Windows 安装包包含运行时。发布阶段使用 GitHub Actions 自带的 GITHUB_TOKEN，构建阶段不使用该令牌。

V59 及之后的客户端可通过设置检查更新；更早版本需要手动安装新版一次。发布后应核对 Releases 的版本、附件和 SHA-256，以及客户端的更新检查结果。
