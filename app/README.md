# Nexus 外设电量中心 · v68

## 运行

Windows 10/11 x64。正式安装包自带 Python 3.12 与 Tkinter。完整源码 ZIP 需要 Python 3.12 x64（含 Tcl/Tk），无需第三方 Python 包。

在完整源码目录运行：

```powershell
py -3.12 my_devices.py
py -3.12 tray_app.py
py -3.12 -m unittest discover -s tests
```

也可使用附带的启动 BAT；若复制了现有便携版的 runtime 文件夹，会优先使用它。源码包不附个人设备列表，首次运行可通过“搜索设备”加入外设。

## v68

- 统一动画的 60 Hz 目标调度，使用高精度时钟和绝对时间安排下一帧。
- 图片淡化采用 64 个档位的按需缓存，整组移动元素，只更新变化的属性。
- 首次布局分批准备并统一提交；取消和连续切换不会显示过期布局。
- 动画完成后局部刷新，停止时释放高精度计时请求。
- 保留 V67 的界面与设备功能；首次加载或系统繁忙时仍可能出现慢帧。

## v67

- 顶部视图切换和设备选择统一为白色导航带，选中视图使用墨黑胶囊。
- 重绘键盘、鼠标和入耳式耳机图标；设备标签按内容分配宽度，细标记采用三次缓出滑动。
- 修正滑块动画中的文字对比度，以及鼠标焦点事件迟到时误显示键盘焦点框的问题。
- 支持鼠标、Tab、左右键、Home / End、多设备翻页和空设备列表。
- 保留 952×553 固定窗口、三设备同屏、单设备排版、卡片悬停、电量独立更新时间和 V66 的并发查询协调。

## 电量与设备支持

PRO WIRELESS 使用 HID++ 直接读取电压和状态，百分比来自电压换算，无需 G HUB。G304/G305 使用设备分档上报。Beats Studio Buds 使用已配对设备的原生查询，分别显示左耳、右耳和仓的设备上报值，不可读时显示 --。

狼蛛型号与协议验证范围见《AULA机械键盘支持清单.md》，V2D 和 G304 另有协议说明。不同设备的支持程度依协议和实物验证情况而定。

## 代码结构

- `my_devices.py` / `my_devices.pyw`：主界面及 GUI 入口。
- `device_navigation.py` / `view_switcher.py`：导航控件与动画。
- `frame_pacer.py` / `ui_motion.py` / `staged_canvas.py`：帧调度、透明度缓存与分批场景提交。
- `query_coordinator.py` / `device_readers.py` / `reading_ui.py`：查询协调、读数模型与局部刷新。
- `receiver_search.py` / `receiver_scan.py` / `saved_devices.py`：搜索、识别与设备管理。
- `logitech_*` / `aula_*` / `beats_*`：对应设备读取实现。
- `tray_app.py` / `tray_common.py`：后台托盘与偏好设置。
- `update_client.py` / `update_ui.py` / `app_dialogs.py`：GitHub 在线更新和设置面板。
- `assets/`：产品图库和 UI 资源；保留原有来源记录与许可证。
- `tools/`：资源生成脚本；`tests/`：回归测试。

GitHub 仓库中的 app 目录是构建覆盖层；完整源码 ZIP 含有全部产品图和资源。覆盖安装保留设备、偏好和耗电记录。不要将个人 JSON、日志、锁文件或 PID 文件提交到仓库。
