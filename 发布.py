#!/usr/bin/env python3
"""拼豆店看板 · 一键发布工具（模仿天胡麻将的发布链路）

流程：
  版本体检 → 本地 Chrome 冒烟 → Firebase 读写检查 → GitHub 权限检查
  → 取发布锁 → 原子提交推送 → 等 GitHub Pages 真的返回新版本
  → 写 Firebase __sysVer 广播（打开中的页面自动刷新）→ 写状态/日志

用法：
  python3 发布.py            # 正式发布
  python3 发布.py --check    # 只体检，不推送
  python3 发布.py --no-tests # 跳过头条 Chrome 冒烟
  python3 发布.py --no-broadcast  # 推送并等线上，但不写版本广播
"""
import argparse, base64, hashlib, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
OWNER, REPO, BRANCH = "Charles011031", "PinDouShop", "main"
LIVE = "https://charles011031.github.io/PinDouShop/"
FILES = ["index.html", "README.md", "版本记录.md", "生成更新日志.py", "更新日志规范.md", "发布.py", "发布.command"]
TOKEN_FILE = pathlib.Path.home() / ".dsh-github-token"
API = "https://api.github.com"
STATE_FILE = ROOT / ".发布状态.json"
LOCK_FILE = ROOT / ".发布锁"
LOG_DIR = ROOT / "发布日志"
LOG_FILE = LOG_DIR / "当前.log"
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]


class Fail(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def set_state(state, step="", extra=None):
    data = {"state": state, "step": step, "ver": VERSION, "pid": os.getpid(),
            "updatedAt": int(time.time())}
    if extra:
        data.update(extra)
    try:
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        LOG_DIR.mkdir(exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s · %s · %s\n" % (time.strftime("%H:%M:%S"), state, step, VERSION))
    except Exception:
        pass


def die(msg, code=1):
    raise Fail(code, msg)


def read_version():
    global VERSION
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    m = re.search(r'const APP_VER\s*=\s*"([^"]+)"', html)
    if not m:
        die("index.html 里找不到 APP_VER")
    VERSION = m.group(1)
    checks = []
    meta = re.search(r'<meta name="app-ver" content="([^"]+)"', html)
    checks.append(("meta app-ver", meta.group(1) if meta else None))
    text = (ROOT / "版本记录.md").read_text(encoding="utf-8")
    c = re.search(r'^\|\s*\**\s*(v[0-9][^\s|*]*)\s*\**\s*（', text, re.M)
    checks.append(("版本记录.md 第一条", c.group(1) if c else None))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    r = re.search(r'当前版本\**\s*[：:]\s*(v[0-9][0-9.]*)', readme)
    checks.append(("README 当前版本", r.group(1) if r else None))
    t = re.search(r'^\|\s*\*\*(v[0-9][^\s|*]*)', readme, re.M)
    checks.append(("README 版本表第一条", t.group(1) if t else None))
    print("• 版本体检：%s" % VERSION)
    bad = [(n, v) for n, v in checks if v and v != VERSION]
    for n, v in checks:
        print("   %s %s%s" % ("✓" if v == VERSION else ("·" if v is None else "✗"),
                              n, "" if v == VERSION else (" = %s" % v if v else "（未写，跳过）")))
    if bad:
        die("版本号不一致：" + "；".join("%s=%s" % x for x in bad))
    if not re.match(r'^v\d+\.\d+\.\d+$', VERSION):
        die("版本号格式应为 vX.Y.Z：%s" % VERSION)
    if "pindoushop-default-rtdb" not in html:
        die("FB_URL 不是 pindoushop 项目，拒绝发布")
    if "tianhu-fd795" in html:
        die("页面里还残留天胡 Firebase 地址，拒绝发布")
    return html


def check_changelog():
    print("• 更新日志检查：")
    try:
        r = subprocess.run([sys.executable, str(ROOT / "生成更新日志.py"), "--check"],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        die("运行 生成更新日志.py 失败：%s" % e)
    out = (r.stdout or r.stderr or "").strip()
    if r.returncode != 0:
        die("更新日志未同步：%s\n  先运行：python3 生成更新日志.py" % out)
    print("   ✓ " + (out.splitlines()[-1].lstrip("✓ ").strip() if out else "已同步"))


def check_files():
    print("• 文件检查：")
    for name in FILES:
        p = ROOT / name
        if not p.exists():
            die("缺少文件：%s" % name)
        if p.stat().st_size == 0:
            die("空文件：%s" % name)
        print("   ✓ %s（%.1f KB）" % (name, p.stat().st_size / 1024))


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and pathlib.Path(c).exists():
            return c
    return None


def chrome_smoke():
    chrome = find_chrome()
    if not chrome:
        print("• Chrome 冒烟：跳过（没找到 Chrome）")
        return
    print("• Chrome 冒烟：")
    tmp = tempfile.mkdtemp(prefix="pindou-chrome-")
    try:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--allow-file-access-from-files", "--user-data-dir=" + tmp,
               "--virtual-time-budget=4000", "--dump-dom",
               (ROOT / "index.html").as_uri()]
        r = subprocess.run(cmd, capture_output=True, timeout=40)
        html = r.stdout.decode("utf-8", "replace")
        if not html:
            die("Chrome 没返回 DOM")
        seats = len(re.findall(r'class="seat ', html))
        if seats < 10:
            die("Chrome 冒烟失败：只渲染出 %d 个工位" % seats)
        if ('content="%s"' % VERSION) not in html:
            die("Chrome 冒烟失败：页面版本不是 %s" % VERSION)
        print("   ✓ 10 个工位、版本 %s、DOM 正常" % VERSION)
    except subprocess.TimeoutExpired:
        die("Chrome 冒烟超时")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fb_urls(html):
    m = re.search(r'const FB_URL = "([^"]+)"', html)
    if not m:
        die("index.html 里找不到 FB_URL")
    state = m.group(1)
    root = state.replace("/state.json", "")
    return state, root + "/__sysVer.json", root + "/__release_probe.json"


def check_firebase(html):
    print("• Firebase 检查：")
    state_url, ver_url, probe_url = fb_urls(html)
    for name, url in (("state", state_url), ("__sysVer", ver_url)):
        try:
            r = urllib.request.urlopen(url + "?t=" + str(int(time.time())), timeout=15)
            if r.status != 200:
                die("%s 读取失败：HTTP %s" % (name, r.status))
            print("   ✓ %s 可读" % name)
        except urllib.error.HTTPError as e:
            die("%s 读取失败：HTTP %s（检查 Realtime Database 规则）" % (name, e.code))
        except Exception as e:
            die("%s 读取失败：%s" % (name, e))
    try:
        payload = json.dumps({"ts": int(time.time() * 1000), "by": "release-check"}).encode()
        req = urllib.request.Request(probe_url + "?t=" + str(int(time.time())), data=payload,
                                     method="PUT", headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=15)
        if r.status not in (200, 201):
            die("Firebase 写入检查失败：HTTP %s" % r.status)
        urllib.request.urlopen(urllib.request.Request(probe_url, method="DELETE"), timeout=15)
        print("   ✓ 写入/删除探针正常")
    except urllib.error.HTTPError as e:
        die("Firebase 写入检查失败：HTTP %s（检查测试模式是否过期/规则）" % e.code)
    except Exception as e:
        die("Firebase 写入检查失败：%s" % e)


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


def check_github():
    print("• GitHub 检查：")
    tok = token()
    code, me = gh("/user", tok)
    if code != 200:
        die("token 无效：HTTP %s" % code)
    code, repo = gh("/repos/%s/%s" % (OWNER, REPO), tok)
    if code != 200:
        die("仓库不可访问：%s/%s HTTP %s" % (OWNER, REPO, code))
    if not (repo.get("permissions") or {}).get("push"):
        die("token 没有 %s/%s 的写权限" % (OWNER, REPO))
    code, pages = gh("/repos/%s/%s/pages" % (OWNER, REPO), tok)
    if code != 200:
        die("GitHub Pages 未开启或 token 无 Pages 读权限；先到仓库 Settings → Pages 开启")
    print("   ✓ 用户 %s / 仓库可写 / Pages 已开启" % me.get("login"))
    return tok


def blob_sha(data):
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def remote_tree(tok):
    code, ref = gh("/repos/%s/%s/git/ref/heads/%s" % (OWNER, REPO, BRANCH), tok)
    if code != 200:
        die("读不到 %s 分支：HTTP %s" % (BRANCH, code))
    head = ref["object"]["sha"]
    code, commit = gh("/repos/%s/%s/git/commits/%s" % (OWNER, REPO, head), tok)
    if code != 200:
        die("读不到 head commit：HTTP %s" % code)
    tree_sha = commit["tree"]["sha"]
    code, tree = gh("/repos/%s/%s/git/trees/%s?recursive=1" % (OWNER, REPO, tree_sha), tok)
    if code != 200:
        die("读不到 tree：HTTP %s" % code)
    return head, tree_sha, {x["path"]: x["sha"] for x in tree.get("tree", []) if x.get("type") == "blob"}


def push_changes(tok):
    print("• 推送 GitHub（一次 commit）：")
    head, base_tree, remote = remote_tree(tok)
    entries = []
    changed = []
    for name in FILES:
        data = (ROOT / name).read_bytes()
        sha = blob_sha(data)
        if remote.get(name) == sha:
            print("   · %s 未变" % name)
            continue
        code, blob = gh("/repos/%s/%s/git/blobs" % (OWNER, REPO), tok, "POST",
                        {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if code not in (200, 201):
            die("上传 blob 失败：%s HTTP %s" % (name, code))
        entries.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        changed.append(name)
    if not entries:
        print("   ✓ 没有文件变化，跳过 commit")
        return False
    code, tree = gh("/repos/%s/%s/git/trees" % (OWNER, REPO), tok, "POST",
                    {"base_tree": base_tree, "tree": entries})
    if code not in (200, 201):
        die("创建 tree 失败：HTTP %s" % code)
    msg = "release: %s\n\n%s" % (VERSION, ", ".join(changed))
    code, commit = gh("/repos/%s/%s/git/commits" % (OWNER, REPO), tok, "POST",
                      {"message": msg, "tree": tree["sha"], "parents": [head]})
    if code not in (200, 201):
        die("创建 commit 失败：HTTP %s" % code)
    code, res = gh("/repos/%s/%s/git/refs/heads/%s" % (OWNER, REPO, BRANCH), tok, "PATCH",
                   {"sha": commit["sha"], "force": False})
    if code not in (200, 201):
        die("更新分支失败：HTTP %s" % code)
    print("   ✓ 已推送 %d 个文件：%s" % (len(changed), ", ".join(changed)))
    return True


def wait_live(html):
    print("• 等 GitHub Pages 返回 %s …" % VERSION)
    ver_meta = 'content="%s"' % VERSION
    ver_js = 'const APP_VER = "%s"' % VERSION
    last = ""
    for i in range(90):
        try:
            req = urllib.request.Request(LIVE + "?t=" + str(int(time.time())),
                                         headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            body = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
            last = "len=%d" % len(body)
            if ver_meta in body and ver_js in body:
                print("   ✓ 线上已生效（%s）" % last)
                return
        except Exception as e:
            last = str(e)[:70]
        time.sleep(2)
    die("等线上超时，最后状态：%s" % last, 3)


def broadcast(html):
    print("• 写入 Firebase 版本广播 …")
    _, ver_url, _ = fb_urls(html)
    payload = json.dumps({"ver": VERSION, "ts": int(time.time() * 1000)}).encode()
    req = urllib.request.Request(ver_url, data=payload, method="PUT",
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        die("写 __sysVer 失败：HTTP %s" % e.code, 4)
    if r.status not in (200, 201):
        die("写 __sysVer 失败：HTTP %s" % r.status, 4)
    check = json.load(urllib.request.urlopen(ver_url + "?t=" + str(int(time.time())), timeout=15))
    if not check or check.get("ver") != VERSION:
        die("__sysVer 回读不一致：%s" % check, 4)
    print("   ✓ 已广播 %s（打开中的页面 20 秒内自动刷新）" % VERSION)


def acquire_lock():
    if LOCK_FILE.exists():
        try:
            info = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            pid = int(info.get("pid", 0))
            age = time.time() - float(info.get("ts", 0))
            if pid and age < 900:
                try:
                    os.kill(pid, 0)
                    die("另一个发布正在进行（pid=%s，%ds 前开始）；确认没在跑就删 %s" % (pid, int(age), LOCK_FILE))
                except ProcessLookupError:
                    pass
            LOCK_FILE.unlink()
        except Fail:
            raise
        except Exception:
            LOCK_FILE.unlink(missing_ok=True)
    LOCK_FILE.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")


def release_lock():
    try:
        LOCK_FILE.unlink()
    except Exception:
        pass


def main():
    global VERSION
    VERSION = "?"
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-tests", action="store_true")
    ap.add_argument("--no-broadcast", action="store_true")
    args = ap.parse_args()
    try:
        html = read_version()
        set_state("running", "版本体检")
        check_changelog()
        check_files()
        if not args.no_tests:
            chrome_smoke()
        check_firebase(html)
        tok = check_github()
        if args.check:
            set_state("checked", "只体检")
            print("✓ 体检通过（未推送）")
            return 0
        acquire_lock()
        set_state("running", "推送")
        pushed = push_changes(tok)
        set_state("running", "等线上生效")
        if pushed:
            wait_live(html)
        else:
            print("• 没有新提交，跳过等线上")
        if not args.no_broadcast:
            set_state("running", "广播版本")
            broadcast(html)
        set_state("done", "发布完成")
        print("✓ 发布完成：%s" % LIVE)
        return 0
    except Fail as e:
        set_state("failed", str(e))
        log("✗ " + str(e))
        return e.code
    except KeyboardInterrupt:
        set_state("failed", "手动中断")
        log("✗ 手动中断")
        return 130
    except Exception as e:
        set_state("failed", "异常：" + repr(e))
        log("✗ 未预期异常：" + repr(e))
        return 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
