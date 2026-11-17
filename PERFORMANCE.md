# v69 GPU 动画加速

v69 增加硬件专用的 Direct2D 内容画面合成，保留 v68 的 Tk 布局与交互。
首次初始化在后台完成，不访问 Tk 对象；未就绪或发生异常时回退原渲染。
默认开启，可在设置中关闭。每阶段上传一次画面，空闲时不持续渲染。

本机 Windows / RTX 3060 / Python 3.12 / 三设备真实 Tk 窗口对照：
软件绘制回调中位数 2.0742 ms、P95 4.3019 ms；GPU 路径中位数
0.3160 ms、P95 0.4390 ms。GPU 回调间隔中位数 16.4203 ms；首次捕获
6.2278 ms，捕获中位数 7.5736 ms、最大 13.5998 ms。首次硬件初始化
已在后台完成。259 个 GPU 帧共上传 18 次画面，播放期间未生成 CPU 淡入淡出图片。
这是回调/提交耗时，不是 GPU 完成时间或屏幕呈现 FPS。调度目标仍为 60 Hz。

验证：261 项回归检查、6 组布局、8 次连续中断、实际硬件路径及模拟 GPU
不可用的自动回退。截图工具故障，人工视觉验收未完成。

硬件专用目标及线程模型依据：
[HARDWARE](https://learn.microsoft.com/en-us/windows/win32/api/d2d1/ne-d2d1-d2d1_render_target_type)、
[多线程工厂](https://learn.microsoft.com/en-us/windows/win32/direct2d/multi-threaded-direct2d-apps)。

# V68 动画性能

V68 保留 Tkinter 界面，针对调度、重复绘制和首次布局构建做优化。

## 测量

在同一台 Windows 电脑、嵌入式 Python 3.12 和 Tk 8.6 下，关闭测试实例的硬件查询与配置写入，运行真实 Tk 事件循环：

| 动画回调间隔 | 原实现 | V68 |
| --- | ---: | ---: |
| 中位数 | 30.985 ms | 16.611 ms |
| P95 | 31.872 ms | 17.534 ms |

实际窗口中另测得设备切换中位数约 16.7 ms、悬停约 16.5 ms。首次构建期间，原同步调用曾阻塞约 239 ms；分批后最慢单次工作约 44 ms。实际窗口仍有少量 33–58 ms 的间隔。

这些数据是动画回调和工作单元耗时，不是 GPU 呈现帧率，不保证每台电脑或每个场景始终锁定 60 FPS。首次字体/图片加载、窗口被遮挡或系统繁忙可能影响结果。

## 验证

- 248 项单元回归检查。
- 真实 Tk 场景原子提交、取消回滚、缩放及旧场景坐标保持。
- 本地三设备总览与详情共 6 组布局的文字、坐标及边界对照。
- 连续 8 次中断切换，无过期场景、失效画布项或计时请求遗留。
- Windows 发布流程另外检查 GUI、实际安装内容及覆盖安装保留用户数据。

高精度计时只在动画播放期间启用，并遵循 [Windows timeBeginPeriod/timeEndPeriod 配对要求](https://learn.microsoft.com/en-us/windows/win32/api/timeapi/nf-timeapi-timebeginperiod)。
