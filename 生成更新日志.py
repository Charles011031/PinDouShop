#!/usr/bin/env python3
"""从 README「### 最近 15 个版本」生成页面里的 APP_CHANGELOG。

用法：
  python3 生成更新日志.py          # 生成并写回 index.html
  python3 生成更新日志.py --dry    # 只打印解析结果
  python3 生成更新日志.py --check  # 只检查是否已同步（发布体检用），落后返回 3
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent
README = ROOT / "README.md"
APP = ROOT / "index.html"
MAX_ITEMS = 15
BEGIN = "// >>> APP_CHANGELOG BEGIN（自动生成，勿手改）"
END = "// <<< APP_CHANGELOG END（自动生成，勿手改）"

SEC_RE = re.compile(r"^###\s*最近\s*15\s*个版本\s*$", re.M)
ROW_RE = re.compile(r"^\|\s*\*\*(v[0-9]+(?:\.[0-9]+)*)\*\*（(\d{4}-\d{2}-\d{2})）\s*\|\s*(.+?)\s*\|\s*$")
CELL_RE = re.compile(r"^摘要[:：]\s*(.*?)\s*[。.]?\s*详情[:：]\s*(.*)$", re.S)


def parse_rows(text):
    m = SEC_RE.search(text)
    if not m:
        raise SystemExit("✗ README.md 里找不到「### 最近 15 个版本」")
    rows = []
    for line in text[m.end():].splitlines():
        if line.startswith("### ") or line.startswith("## "):
            break
        r = ROW_RE.match(line)
        if r:
            rows.append((r.group(1), r.group(2), r.group(3).strip()))
        if len(rows) >= MAX_ITEMS:
            break
    if not rows:
        raise SystemExit("✗ README 的版本表里没有可解析的行")
    return rows


def parse_cell(text):
    m = CELL_RE.match(text.strip())
    if m:
        summary = m.group(1).strip()
        items = [x.strip() for x in re.split(r"[；;]", m.group(2)) if x.strip()]
        return summary, items
    return "", [x.strip() for x in re.split(r"[；;]", text) if x.strip()]


def js_block(rows):
    out = [BEGIN, "const APP_CHANGELOG = ["]
    for ver, date, text in rows:
        summary, items = parse_cell(text)
        out.append('  { ver: %s, date: %s, sum: %s, items: [%s] },' % (
            json.dumps(ver, ensure_ascii=False),
            json.dumps(date, ensure_ascii=False),
            json.dumps(summary, ensure_ascii=False),
            ", ".join(json.dumps(x, ensure_ascii=False) for x in items)))
    out.append("];")
    out.append(END)
    return "\n".join(out)


def apply(html, block):
    i = html.find(BEGIN)
    j = html.find(END)
    if i >= 0 and j > i:
        return html[:i] + block + html[j + len(END):], "替换"
    m = re.search(r'^const APP_VER = "[^"]+", APP_DATE = "[^"]+";[ \t]*$', html, re.M)
    if not m:
        raise SystemExit("✗ 找不到 APP_VER 行，也没有更新日志标记")
    return html[:m.end()] + "\n" + block + html[m.end():], "插入"


def main(argv):
    dry = "--dry" in argv
    check = "--check" in argv
    rows = parse_rows(README.read_text(encoding="utf-8"))
    block = js_block(rows)
    if "</script" in block.lower():
        raise SystemExit("✗ 生成内容里出现 </script，可能截断脚本")
    html = APP.read_text(encoding="utf-8")
    new_html, how = apply(html, block)
    if dry:
        print("解析到 %d 条：%s → %s" % (len(rows), rows[0][0], rows[-1][0]))
        for ver, date, text in rows:
            summary, items = parse_cell(text)
            print("  %s %s  %d 条  摘要：%s" % (ver, date, len(items), summary or "（空）"))
        return 0
    if check:
        if new_html == html:
            print("✓ 内嵌更新日志已是最新（%d 条，%s）" % (len(rows), rows[0][0]))
            return 0
        print("⚠️ 内嵌更新日志和 README 不一致：先运行 python3 生成更新日志.py")
        return 3
    if new_html != html:
        APP.write_text(new_html, encoding="utf-8")
        print("✓ 已生成 %d 条更新日志（%s）" % (len(rows), rows[0][0]))
    else:
        print("✓ 已经是最新（%d 条，%s）" % (len(rows), rows[0][0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
