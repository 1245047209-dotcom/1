# F87 Pro V2D 电量读取（v58）

新增 AULA F87 Pro V2D / AULA F87ProV2D Dongle 的 2.4G 电量查询。
硬件标识为 `0C45:FEFE`，同时要求系统型号包含 F87 Pro V2D。
这个 USB 标识也用于其他 SONiX 键盘，因此不会仅凭标识向其他型号发送查询。

## 协议依据

参考 JKWTCN/BatteryMonitor（MIT）：
https://github.com/JKWTCN/BatteryMonitor/blob/50f628c1ae1ced004e9fa03737cee9d6a561d80e/src/providers/hid/AulaHidProvider.cpp
https://github.com/JKWTCN/BatteryMonitor/blob/50f628c1ae1ced004e9fa03737cee9d6a561d80e/src/providers/hid/AulaHidProvider.h

上游明确列出 F87ProV2D 已验证。本项目使用 Python 与 Windows HID 接口重新实现，
没有在本机实测 F87ProV2D 硬件；最终读数需要该型号实机确认。

- 仅选择所加入接收器自己的厂商 HID 接口，按描述符的输入/输出报告长度发送。
- Output/Input Report，Report ID 0；请求标志 AA，回复标志 55。
- 设备信息查询：优先 10/56，再尝试 10/48，最后 A0/64 兼容格式。
- 八字节头部；每页按地址拼接。新格式最后一页设置尾包标志，旧格式不设置。
- 设备信息偏移 16 为工作模式、17 为百分比、18 为充电标志。
- 丢弃错误命令、错误分页地址、空白、过短、无效百分比以及非 2.4G 工作模式回复。
- 只发 GET_DEVICE_INFO 查询，不发改键、灯光、配对、配置写入或固件命令。
- 查询失败显示未知；不使用旧缓存充当新读数，也不将空白回复显示为 0%。

## 使用和升级

1. 运行 v58 安装程序覆盖安装，保留原设备列表及历史数据。
2. 打开主界面，点击“搜索设备”，加入 F87 Pro V2D。
3. v57 已加入的同型号会继续使用原设备 ID 和选择状态，并修正通用厂商名称。
4. 切到 2.4G 模式，按键唤醒后点击刷新。
5. 没有旧版 F87 Pro 在线时，托盘读取已加入的 F87 Pro V2D。

若仍无法读取，可运行安装目录中的 `F87ProV2D读取诊断.bat`。
它会在本机生成 `F87ProV2D-诊断.json`，包含接口报告长度和设备信息查询收发记录，
不记录按键、设备序列号或完整设备实例路径，也不会自动上传报告。
