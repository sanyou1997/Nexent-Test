"""在桌面创建 CTAS 启动/停止快捷方式（.lnk）。

不依赖 WScript.Shell / Shell.Application（COM 在部分环境被安全策略拦截），
手工构造 Windows Shell Link 二进制格式（MS-SHLLINK），纯 Python 实现。

用法：python make_shortcuts.py
"""
import os
import struct
import sys
from pathlib import Path

# ---------- MS-SHLLINK 常量 ----------
LINK_FLAG_HAS_NAME = 0x00000004
LINK_FLAG_HAS_REL_PATH = 0x00000010
LINK_FLAG_HAS_WORKING_DIR = 0x00000040
LINK_FLAG_HAS_ICON_LOC = 0x00000080
LINK_FLAG_IS_UNICODE = 0x00000080

FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_TIME_INVALID = 0xFFFFFFFFFFFFFFFF

# LinkCLSID: 00021401-0000-0000-C000-000000000046（Shell Link CLSID，16 字节 GUID）
LINK_CLSID = bytes([
    0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46,
])

VOLUME_IS_FIXED = 0x00000001
SW_SHOWNORMAL = 1


def _dos_date_time():
    """构造 DOS 日期/时间对（本地时间）。"""
    import time
    now = time.localtime()
    year = max(1980, now.tm_year)
    date = ((year - 1980) << 9) | (now.tm_mon << 5) | now.tm_mday
    tm = (now.tm_hour << 11) | (now.tm_min << 5) | (now.tm_sec // 2)
    return date, tm


def build_lnk(target: str, working_dir: str = "", description: str = "",
              icon_path: str = "", icon_index: int = 0, arguments: str = "") -> bytes:
    """构造 .lnk 文件字节（纯 Python，无 COM）。"""
    target_abs = os.path.abspath(target)
    wd_abs = os.path.abspath(working_dir) if working_dir else os.path.dirname(target_abs)

    def enc(s: str) -> bytes:
        return s.encode("utf-16-le")

    name_data = enc(os.path.basename(target_abs))
    rel_data = enc(target_abs)
    wd_data = enc(wd_abs)
    args_data = enc(arguments)
    icon_data = enc(icon_path)

    file_size = os.path.getsize(target_abs) if os.path.isfile(target_abs) else 0

    flags = (LINK_FLAG_HAS_NAME | LINK_FLAG_HAS_REL_PATH
             | LINK_FLAG_HAS_WORKING_DIR | LINK_FLAG_HAS_ICON_LOC | LINK_FLAG_IS_UNICODE)

    # --- LinkHeader（76 字节，逐字段精确打包） ---
    # 偏移表：+00 HeaderSize(4) +04 LinkCLSID(16) +14 LinkFlags(4) +18 FileAttributes(4)
    #         +1C CreationTime(8) +24 AccessTime(8) +2C WriteTime(8) +34 FileSize(4)
    #         +38 IconIndex(4) +3C ShowCommand(4) +40 HotKey(2) +42 Reserved1(2)
    #         +44 Reserved2(4) +48 Reserved3(4)  => 共 0x4C = 76 字节
    h = b""
    h += struct.pack("<I", 0x0000004C)                          # HeaderSize
    h += LINK_CLSID                                           # LinkCLSID (16 字节 GUID)
    h += struct.pack("<I", flags)                               # LinkFlags
    h += struct.pack("<I", FILE_ATTRIBUTE_NORMAL)               # FileAttributes
    h += struct.pack("<Q", FILE_TIME_INVALID)                   # CreationTime
    h += struct.pack("<Q", FILE_TIME_INVALID)                   # AccessTime
    h += struct.pack("<Q", FILE_TIME_INVALID)                   # WriteTime
    h += struct.pack("<I", file_size)                           # FileSize
    h += struct.pack("<i", icon_index)                          # IconIndex
    h += struct.pack("<I", SW_SHOWNORMAL)                       # ShowCommand
    h += struct.pack("<H", 0)                                   # HotKey
    h += struct.pack("<H", 0)                                   # Reserved1
    h += struct.pack("<I", 0)                                   # Reserved2
    h += struct.pack("<I", 0)                                   # Reserved3

    # --- StringData 段 ---
    b = b""
    b += struct.pack("<I", len(name_data)) + name_data          # NAME_STRING
    b += struct.pack("<I", len(rel_data)) + rel_data            # RELATIVE_PATH
    b += struct.pack("<I", len(wd_data)) + wd_data              # WORKING_DIR
    b += struct.pack("<I", len(args_data)) + args_data          # COMMAND_LINE_ARGUMENTS
    b += struct.pack("<I", len(icon_data)) + icon_data          # ICON_LOCATION

    # --- TerminalID：ANSI 字符串（\0 结尾） ---
    term = target_abs.encode("mbcs", errors="replace") + b"\x00"
    b += struct.pack("<I", len(term)) + term

    return h + b


def main() -> int:
    desktop = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
    script_dir = Path(__file__).resolve().parent
    project = script_dir.parent.parent          # backend/scripts → 项目根
    if not (project / "启动CTAS.bat").exists():
        project = Path(r"D:\workbuddy-workspace\ctas-system")

    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    shell32 = os.path.join(system_root, "System32", "shell32.dll")

    items = [
        ("CTAS 审理辅助系统.lnk", project / "启动CTAS.bat",
         "启动审理辅助系统（本地大模型 + 业务服务 + 自动打开浏览器）", 13),
        ("CTAS 停止服务.lnk", project / "停止CTAS.bat",
         "停止审理辅助系统的业务服务与本地模型服务", 28),
    ]

    ok = True
    for name, target, desc, icon_idx in items:
        if not target.exists():
            print(f"  x 目标不存在，跳过：{target}")
            ok = False
            continue
        try:
            data = build_lnk(str(target), str(project), desc, shell32, icon_idx)
            out = desktop / name
            out.write_bytes(data)
            print(f"  v 已创建桌面快捷方式：{out}  ({len(data)} 字节)")
        except Exception as exc:  # noqa: BLE001
            print(f"  x 创建失败 {name}: {type(exc).__name__}: {exc}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
