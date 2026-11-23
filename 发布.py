#!/usr/bin/env python3
"""拼豆店看板 · 一键发布

流程：版本检查 → 推送 GitHub → 等线上真的返回新版本 → 写 Firebase 版本广播（打开中的页面自动刷新）

用法：
  python3 发布.py           # 发布
  python3 发布.py --check   # 只检查版本/仓库/token，不推送
"""
import argparse, base64, json, pathlib, re, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
OWNER, REPO, BRANCH = "Charles011031", "PinDouShop", "main"
LIVE = "https://charles011031.github.io/PinDouShop/"
FILES = ["index.html", "README.md", "版本记录.md", "发布.py"]
TOKEN_FILE = pathlib.Path.home() / ".dsh-github-token"
API = "https://api.github.com"


def die(msg):
    print("✗ " + msg)
    sys.exit(1)


def read_ver():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    m = re.search(r'const APP_VER\s*=\s*"([^"]+)"', html)
    if not m:
        die("index.html 里找不到 APP_VER")
    meta = re.search(r'<meta name="app-ver" content="([^"]+)"', html)
    if meta and meta.group(1) != m.group(1):
        die("APP_VER 与 meta app-ver 不一致：%s / %s" % (m.group(1), meta.group(1)))
    ch = re.search(r'\{ver:"(v[0-9][^"]*)"', html)
    if ch and ch.group(1) != m.group(1):
        die("页面 CHANGELOG 第一条不是 %s（是 %s）" % (m.group(1), ch.group(1)))
    return m.group(1), html


def read_changelog_ver():
    text = (ROOT / "版本记录.md").read_text(encoding="utf-8")
    m = re.search(r'^\|\s*(v[0-9][^\s|]*)\s*\|', text, re.M)
    if not m:
        die("版本记录.md 里找不到版本行")
    return m.group(1)


def read_readme_ver():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r'当前版本\**\s*[：:]\s*(v[0-9][0-9.]*)', text)
    return m.group(1) if m else None


def token():
    if not TOKEN_FILE.exists():
        die("没找到 GitHub token：%s" % TOKEN_FILE)
    return TOKEN_FILE.read_text().strip()


def gh(path, tok, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        "User-Agent": "pindou-release"})
    try:
        r = urllib.request.urlopen(req, timeout=40)
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def push_file(name, text, tok, msg):
    path = "/repos/%s/%s/contents/%s" % (OWNER, REPO, urllib.parse.quote(name))
    code, cur = gh(path, tok)
    sha = cur.get("sha") if code == 200 else None
    body = {"message": msg, "content": base64.b64encode(text.encode("utf-8")).decode(), "branch": BRANCH}
    if sha:
        body["sha"] = sha
    code, res = gh(path, tok, "PUT", body)
    if code not in (200, 201):
        die("推送 %s 失败：HTTP %s %s" % (name, code, res.get("message") or res))
    print("  · %s ✓" % name)


def wait_live(ver):
    print("• 等 GitHub Pages 返回 %s …" % ver)
    last = ""
    for i in range(90):
        try:
            req = urllib.request.Request(LIVE + "?t=" + str(int(time.time())), headers={"Cache-Control": "no-cache"})
            html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
            last = "len=%d" % len(html)
            if ('content="%s"' % ver) in html and ('const APP_VER = "%s"' % ver) in html:
                print("  · 线上已生效（%s）" % last)
                return
        except Exception as e:
            last = str(e)[:60]
        time.sleep(2)
    die("等线上超时，最后状态：%s" % last)


def broadcast(ver, html):
    m = re.search(r'const FB_URL = "([^"]+)"', html)
    if not m:
        die("index.html 里找不到 FB_URL")
    url = m.group(1).replace("/state.json", "/__sysVer.json")
    payload = json.dumps({"ver": ver, "ts": int(time.time() * 1000)}).encode()
    req = urllib.request.Request(url, data=payload, method="PUT", headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        die("写 Firebase 版本节点失败：HTTP %s %s" % (e.code, e.read().decode()[:160]))
    if r.status not in (200, 201):
        die("写 Firebase 版本节点失败：HTTP %s" % r.status)
    print("• 已广播版本 %s → %s" % (ver, url.split("//")[1].split("/")[0]))
    print("  打开中的页面会在 20 秒内自动刷新（有弹窗/在计时时先弹提示条）。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    ver, html = read_ver()
    chver = read_changelog_ver()
    rver = read_readme_ver()
    print("• 版本：index=%s 版本记录=%s README=%s" % (ver, chver, rver or "（未写）"))
    if chver != ver:
        die("版本记录.md 第一条不是 %s" % ver)
    if rver and rver != ver:
        die("README「当前版本」不是 %s" % ver)
    tok = token()
    code, me = gh("/user", tok)
    if code != 200:
        die("GitHub token 无效：HTTP %s" % code)
    code, repo = gh("/repos/%s/%s" % (OWNER, REPO), tok)
    if code != 200 or not (repo.get("permissions") or {}).get("push"):
        die("没有 %s/%s 的写权限：HTTP %s" % (OWNER, REPO, code))
    print("• 仓库：%s/%s ✓" % (OWNER, REPO))
    if args.check:
        print("✓ 检查通过（未推送）")
        return
    print("• 推送 GitHub …")
    push_file("index.html", html, tok, "release: %s" % ver)
    for name in FILES[1:]:
        push_file(name, (ROOT / name).read_text(encoding="utf-8"), tok, "release: %s" % ver)
    wait_live(ver)
    broadcast(ver, html)
    print("✓ 发布完成：%s" % LIVE)


if __name__ == "__main__":
    main()
