# RTX 4070 Ti SUPER：Windows UEFI 适配

[English source/build guide](README.md) ·
[首次 Windows 实机报告](../docs/windows-ad103-2705-report.md) ·
[公开测试证据](../docs/evidence/windows-ad103-2705/README.md)

本分支已完成 RUN00、RUN01 的只读冷启动采集。只读探针位于 `uefi/`；独立的
实验解锁实现位于 `unlock/`，根据这张板卡、MSI Z590 的 UEFI 驱动及系统启动
文件建立精确配置。**2026-10-25，本机首次手动 U 启动已通过硬件读回、
Windows 驱动接管和指令吞吐复测。**

实验版需手动按 U 才执行一次解锁事务，默认等待 30 秒后启动原始 Windows。
本次结果只覆盖这台机器的一次启动；重复启动、睡眠恢复与实际应用收益尚未验证。
原构建清单保留构建时的 `hardware_tested: false`，后续实机证据单独记录，不改写
原始构建和采集文件。

已在当前 Windows 主机读到：

| 项目 | 结果 |
|---|---|
| GPU | RTX 4070 Ti SUPER，`10de:2705` |
| 子系统 | `6688:2705` |
| PCI 地址 | `0000:01:00.0`（以 UEFI 实际采集值为准） |
| VBIOS | `95.03.45.40.a4` |
| NVIDIA 驱动 | `617.42` |
| 系统 | Windows 10 22H2，build 19045 |
| 主板 | MSI MPG Z590 GAMING CARBON WIFI |
| Secure Boot | Windows 注册表显示关闭；UEFI 设置仍以主板界面为准 |

当前采集程序为 **v3**。保留 v2 的 UEFI 驱动归属、加载代码与 Windows 启动
文件采集，并补齐与上游冷启动档对照所需的 9 个寄存器读数；现在共记录 21 个
GPU 寄存器。Windows 文件系统枚举失败会记录索引、阶段、状态及可用的设备路径。上游分别报告过 RTX 4080 AD103
32GB 和 MSI RTX 4070 Ti AD104 的 Windows 启动实验。当前这张 4070 Ti SUPER、
Windows 10、驱动 617.42 的组合已有一次手动解锁启动实测，数据见下文。

## 实验解锁版

RUN01 已采齐 21 项冷启动寄存器。ROM、PCI 配置、3 个 UEFI 驱动代码区和
Windows 启动文件与 RUN00 一致。原始采集仍报告一项文件系统不完整：UEFI
对一块非系统 NTFS 数据分区调用 OpenVolume 时返回 EFI_NOT_FOUND。
`prepare_2705_profile.py` 将它与 Windows 分区元数据逐项核对；系统 ESP、
分区位置与启动文件哈希均匹配。原始记录保留不完整状态，独立的本地配置档
记录这一个具体异常的处理依据，不自动授权解锁。

本地构建命令：

```powershell
python windows/build_probe.py --deps-only
python windows/prepare_assets.py
python windows/build_unlock.py --check --run build/windows/captures/RUN01 --partition-review build/windows/partition-review.json --fwsec build/windows/fwsec-analysis.json
python -m unittest discover -s tests -p 'test_windows_*.py' -v
```

命令中的三个输入路径需要换成实际审查过的本地文件；它们不随源码发布。
已有固定版本依赖及输入缓存时，固件准备可以加 `--offline`。首次完整构建需
准备该机器的采集、独立 Windows 分区核对和 PROM 的 FWSEC 离线分析；这些
输入及准入要求见 [English source/build guide](README.md)。

固件准备使用固定输入 SHA-256 和已有 Zig 标准库，生成 5 个运行时固件文件。
私有输入及输出均位于忽略的 `build/windows/`。构建输出为
`build/windows/unlock/usb/`，它与 `build/windows/probe-usb/` 的只读程序分开。
只有通过实际源码的模拟检查后才安装实验版。现有内部系统 ESP 和启动项不修改。

实验版的操作流程：

1. 保存工作，Shift + 关机；完全关闭后断电约 30 秒。保持测试 U 盘及其他 USB
   外设连接方式不变，开机按 MSI F11，选择 Kingston 的 UEFI 启动项。
2. 确认界面显示 `AD103 2705 / Z590 EXPERIMENTAL v0.1`。按 **U** 执行一次
   实验；按 **S**、Esc 或等待 30 秒则保持显卡原始状态并启动 Windows。
   若配置检查失败，程序返回固件，选择原 Windows Boot Manager。
3. 按 U 后显示画面可能停留在原处；等待 Windows 接管，期间不插拔外设。
   若 4 分钟仍未进入 Windows，完全断电约 30 秒，再从主板菜单选择原 Windows
   Boot Manager。返回 Windows 后先分析 USB 日志，避免未经核对的重复尝试。
4. 日志保存在 U 盘 `AD103-2705-UNLOCK/RUN0001.TXT`（之后使用新编号）。
   `GPU-UNLOCK-2705/RUN00`、`RUN01` 保留原样。进入 Windows 后仍需确认驱动
   正常、读取日志并测量实际指令吞吐；单看进入系统不能证明加速成立。

程序保留 SEC2 停止、载荷标记、独立权限读回、速度选择读回、FLR 与 DMA
清理检查。无法确认固件停止时会保留缓冲区并停机，不进行自动重试。
代码改动与来源见 [unlock/CHANGES.md](unlock/CHANGES.md)。

## 解锁前后对照

本机 CUDA Toolkit 11.8 可以构建此 SM89 指令测试，本轮对照保持工具链、
驱动、功耗与频率设置一致。系统 Toolkit 与 TensorRT/PyTorch 环境中的运行库
分别管理；本次硬件解锁不要求升级它们。

```powershell
python windows/benchmark_tensor.py --label baseline
# 解锁尝试后先分析 UEFI 日志、确认 Windows 驱动正常，再运行：
python windows/benchmark_tensor.py --label after-attempt
```

程序保存编译好的同一份 cubin，预热后运行 3 轮，每轮各测 5 次 FP16→FP32、
TF32 和无符号 INT4 指令吞吐，并检查每个输出元素。测试只执行常规 CUDA
计算。GPU 计时图减少主机提交间隙；频率/功耗在预热和正式运行之间记录，
不在正式计时期间并发轮询 nvidia-smi；对比时保持测试设置和桌面负载一致。

结果和最新基线指针位于 `build/windows/benchmarks/`。后测会校验 GPU UUID、
驱动、功耗上限、测试二进制哈希与运行参数，保存各项中位数和相对基线的比值。
这些是寄存器内指令吞吐；VS-DRBA 的端到端收益需要另外固定片源和配置测量。

2026-10-25 首次实机启动后的两轮复测，每轮每模式 15 个计时样本；
基线每模式 15 个样本。以下解锁后数字为两轮全部 30 个样本的中位数：

| 模式 | 解锁前 | 解锁后 | 提升 |
|---|---:|---:|---:|
| FP16→FP32，TFLOP/s | 88.17 | 149.59 | 69.7% |
| TF32，TFLOP/s | 44.37 | 76.22 | 71.8% |

两轮全部计时及对照模式记录见[公开测试证据](../docs/evidence/windows-ad103-2705/README.md)。

日志确认 `0x67b00001` 标记、必需权限、SEC2 停止、SS0 `0x88888888` / SS1 `0x8`、
FLR 后有效速度读回及 DMA 清理通过；Windows 设备错误码为 0，驱动为 617.42。
最后的 Windows 前检查在 USB 日志关闭后执行，没有直接日志行；用户进入 Windows、
驱动状态和后续 CUDA 实测共同支持本次交接成功。

测试期间正常使用 Windows 桌面，频率信息在计时前后采样。
所有全 1 输入检查通过；本次未直接测 BF16，不建立一般数值正确性或
VS-DRBA 端到端收益。一次启动后的两轮测试也不等同于多次启动验证。
公开附件只包含事务日志、选定的构建元数据和完整计时数字；私有采集及
完整本地审查仍保存在忽略的 `build/windows/`，原始文件不改写。

## 重新进行只读采集（需要时）

1. 在仓库根目录构建，或使用已生成的文件：

   ```powershell
   python windows/build_probe.py
   ```

   U 盘目录位于 `build/windows/probe-usb/`。构建检查只验证编译和 PE/UEFI
   文件格式。v2/v3 均已在本机取得采集结果；只读诊断仍不执行解锁。

2. 准备一个现有 FAT32 U 盘，将上述目录的**内容**复制到 U 盘根目录：

   ```text
   EFI/BOOT/BOOTX64.EFI
   GPU-UNLOCK-2705/
   PROBE-README.txt
   ```

   保留 U 盘原来已有的 `EFI/BOOT/BOOTX64.EFI`。这里的目标是可移动 U 盘，
   Windows 系统盘的 EFI 分区不参与这一步。若系统盘启用 BitLocker，先确认
   恢复密钥可用；这份工具无法在当前非管理员进程中确认其保护状态。

3. 先正常保存工作。为后续配置档取得冷启动状态，在 Windows 按住 Shift
   选择关机，完全关机后切断主机电源约 30 秒再开机。此 MSI 主板按 F11 进入一次性启动
   菜单，选择该 U 盘的 UEFI 项。程序会保存诊断结果，随后按键或等待
   30 秒返回主板固件。开头应显示 `probe_version=3` 和 `mode=gpu_read_only`。
   等待末尾 `probe_status=0x0000000000000000` 与按键提示后再返回。
   状态 0 表示采集和文件保存步骤完成，资料完整性仍由离线工具核验。
   它不修改启动顺序，也不负责直接启动 Windows；
   按需要通过主板菜单选择原来的 Windows Boot Manager。

4. 回到 Windows，查看 U 盘的 `GPU-UNLOCK-2705/RUN00/`，再次运行会使用下一个
   空闲编号。假设 U 盘盘符为 E：

   ```powershell
   python windows/read_probe.py E:\GPU-UNLOCK-2705\RUN00
   ```

   工具将配置草稿写到 `build/windows/board-profile.json`。失败或部分采集也会
   保留可用诊断，不能将部分采集认作成功。

   如果已经用 GPU-Z 导出了**这张卡自己的完整 VBIOS**，可以一并核对 ROM/PROM
   映射。将文件保存在 `build/windows/AD103.rom` 后运行：

   ```powershell
   python windows/read_probe.py E:\GPU-UNLOCK-2705\RUN00 --rom build/windows/AD103.rom
   ```

   `PCI-ROM.BIN` 是固件提供的 PCI ROM 内存副本，可能只包含 UEFI 图像，
   它和 GPU-Z 导出的完整 VBIOS 属于不同输入。

## 采集内容与边界

程序仅选择**一个**匹配 `10de:2705 / 6688:2705` 的设备，记录 PCI 配置、BAR0
布局、GOP 信息、AD103 标识、显存寄存器和解锁相关寄存器，并读取 BAR0
`0x300000` 起的 1 MiB PROM。它采用 EFI PCI I/O 的 BAR 相对读取，不固定
BAR0 的物理地址。

结果包含 `REPORT.TXT`、`PCI-CONFIG.BIN`、`PROM.BIN`，以及固件提供时的
`PCI-ROM.BIN`。v2/v3 还保存 `CONTEXT.JSON` 和找到的 `BOOTMGFW00.BIN` 等文件：

- `CONTEXT.JSON` 记录 UEFI 设备路径、协议占用关系、相关驱动代码及其哈希，
  并读取 Secure Boot、控制台等变量。关系快照不构成驱动释放许可。
- 对内部文件系统只以读取方式打开 `EFI/Microsoft/Boot/bootmgfw.efi`，记录分区
  类型、GUID、起始扇区和长度，保存有大小限制的启动文件副本及哈希。多个候选
  会全部记录，由后续审查确定具体启动目标。
- 新文件均保存到加载本程序的 USB 中，每次创建新的 RUN 目录。完整性检查会
  核对驱动代码、启动文件的字节哈希和分区路径；缺失、不完整或歧义资料保留为
  诊断结果，不会自动成为可用的 Windows 交接配置。

v3 新增的只读地址为 `0x1fa824`、`0x823814`、`0x823818`、`0x625f04`、
`0x1fa7c8`、`0x1fa82c`、`0x1fa830`、`0x824148`、`0x8241e0`。离线结果中的
`windows_transaction_registers_complete` 表示这些数值是否采齐，不代表已允许
解锁。解析器兼容旧版记录，旧 RUN 目录保留原样。

原始 ROM/PROM、固件驱动代码、启动文件和机器路径是本地研究输入，应保留在
U 盘或忽略的 `build/` 目录中。

程序没有 GPU 寄存器写入、PCI 配置写入、固件执行、GPU DMA、FLR、显示驱动
断开/重连或刷写功能。它与前述实验版是两个不同的构建目标。
如果程序没有完成，记录屏幕现象并保留已有 REPORT，不进行解锁尝试。

配置草稿始终标记 `unlock_enabled: false`、`hardware_unlock_tested: false`。
实验版根据单独审查的私有输入建立准入检查，参考新增 Windows 实现适配显示
驱动释放、USB 资源与系统交接，参考 TOTORONG 的 Ada UEFI 流程，并沿用 Linux
Ada 分支的标记、权限、SEC2 停止、速度选择与 FLR 后检查。本机已核对一次 Windows 接管和指令吞吐；重复冷启动、
热重启与睡眠/休眠恢复仍属于后续独立验证项。

## 重新准备构建环境

已准备好的依赖位于 `build/windows/deps/`。Windows 上也可以重新获取固定版本：

```powershell
python windows/build_probe.py --prepare
powershell -NoProfile -ExecutionPolicy Bypass -File windows/collect_host.ps1
```

构建需要 Python 3.11+、Git 和 Zig 0.14.1。可运行下面两组检查：

```powershell
python windows/build_probe.py --check
python -m unittest discover -s tests -p 'test_windows_probe.py' -v
```

第一组在主机上检查实际 C 源码的设备筛选、USB 限制、D0/能力链、BAR 边界、
读写错误、只读启动文件采集，以及上游 PE/驱动归属解析；第二组检查离线采集
数据、ROM、驱动代码哈希和 Windows 启动资料。
这些检查不等同于固件环境或真实 GPU 测试。

准备脚本仅下载构建工具/头文件，
不下载或执行 NVIDIA 固件。Zig ZIP 校验固定 SHA-256，EDK2 头文件来自
TOTORONG 仓库固定版本。Linux/macOS 可用 `--zig /path/to/zig` 指定 0.14.1，
用 `--edk2 /path/to/edk2` 指定已有头文件。

来源：

- [TOTORONG Ada UEFI，固定版本 51df9c17ec89eb8486df52cc9079dde4bd1f9974](https://huggingface.co/TOTORONG/AD103_win_unlocker/tree/51df9c17ec89eb8486df52cc9079dde4bd1f9974)
  提供 Ada 启动前流程的研究依据及所用 EDK2 头文件；头文件保留 BSD-2-Clause-Patent 声明。
- [当前仓库的 2705 Linux 实测](../docs/ada-2705-report.md)提供同型号 GPU 的参考。
- [新增 Windows 参考，固定版本 4e7fefcc4923f9e083d5324dc75a7d2f906af77f](https://github.com/JustAResearcher/gpu-unlock-ada/tree/4e7fefcc4923f9e083d5324dc75a7d2f906af77f/windows/ad104-msi)
  提供只读 UEFI 驱动归属/代码采样组件，保留 MIT 许可证与来源；原样组件位于
  `uefi/vendor/ad104/`，AD104 专用 PCI 筛选函数未被本地探针调用。
- 新诊断集成和脚本采用 GPL-2.0-only。只读探针没有链接解锁代码或载荷；其
  发行包附带上游组件的 `AD104-MIT-LICENSE.txt` 和 `THIRD-PARTY.md`。
- 实验解锁版移植上游 `windows/ad104-msi` 的 GPL-2.0-only 事务、驱动释放与
  Windows 启动实现。独立许可、修改说明和固件许可随实验版提供。
