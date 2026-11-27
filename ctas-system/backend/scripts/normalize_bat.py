"""把 .bat 统一规范化为 cmd 友好格式：GBK 编码 + CRLF 行尾。

cmd.exe 按系统 ANSI 代码页（简体中文 = GBK）解析 .bat，且要求 CRLF 行尾；
若为 UTF-8/LF，会出现中文乱码 + 命令被行中截断（%VAR% 变空、参数被拆断）。
本脚本幂等，可重复执行。
"""
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent.parent


def read_text_any(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="replace")


def normalize(name: str) -> None:
    p = BASE / name
    if not p.exists():
        print(f"  跳过（不存在）：{name}")
        return
    txt = read_text_any(p)
    txt = txt.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    p.write_bytes(txt.encode("gbk", errors="replace"))
    b = p.read_bytes()
    crlf = b.count(b"\r\n")
    bare_lf = b.count(b"\n") - crlf
    print(f"  ✓ {name}: GBK+CRLF, CRLF={crlf}, 裸LF={bare_lf}, {len(b)}字节")


if __name__ == "__main__":
    print("规范化 .bat 编码与行尾：")
    for n in ("启动CTAS.bat", "停止CTAS.bat", "start_ctas_server.bat"):
        normalize(n)
    sys.exit(0)
