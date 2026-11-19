# bx-game

一个没有主页的密码迷宫。访问 `/` 自动跳到玩家自己的当前关卡 `/game/N`；每一关完全是站长自己写的 HTML，服务端只往里注入：**主题背景**、一个密码框、「上一关 / 下一关」。

答案在浏览器本地算成 SHA-256 再提交，服务器只保存和校验哈希，永远收不到答案原文。后台在 `/super_admin`。

## 玩法与路由

1. 打开 `/` → `303` 跳到自己的当前关卡 `/game/N`；全部通关后 `/` 显示「全部关卡已通过。」。
2. 关卡页就是 `copyspace/stepN.html` 的完整 HTML，随便写题面、样式、图片。服务端在返回时注入：
   - 背景主题（`<div class="bx-bg">` + `<style id="bx-style">`），**站长手写的页面同样会注入**；
   - 密码框和两个按钮：

   ```html
   <form class="bx-controls" id="bx-form-N" method="post" action="/api/answer">
     <input type="hidden" name="step" value="N">
     <input type="hidden" name="hash" value="">
     <input type="password" placeholder="密码" autocomplete="off" required>
     <button type="button" onclick="location.href='/game/N-1'">上一关</button>
     <button type="submit">下一关</button>
   </form>
   <script>…内联 SHA-256…</script>
   ```

   - 输入密码后，浏览器本地算 SHA-256 写进 `hash`，密码框本身没有 `name`，明文不会离开浏览器。
   - 答对跳到下一关；答错回到本关并显示「密码不对。」。
   - 想指定控件位置，在页面里写 `<!--bx-controls-->`；不写就插到 `</body>` 前。
3. 所有玩家共享一轮：任何人答对当前关卡，全网就开到下一关；玩家按 Cookie 从第 1 关逐层追上来。
4. 只允许访问自己的当前关：Cookie 关卡不对、访问未来关卡、访问 `/game/9999999` 都会自动跳回当前关。管理员登录状态下可以预览任意关卡。
5. 旧的 `/copyspace/stepN.html` 会自动 `302` 到 `/game/N`，`copyspace/` 下的图片/CSS/JS 仍原样返回。
6. 提交密码的 POST 表单有 **1 KB 上限**：带 `Content-Length` 且超限直接 `413`；流式请求（没有长度）最多读 1 KB 后截断。

## 背景 / 主题

内置 12 种风格：ChatGPT 绿、暗科技、温馨暖色、玻璃拟态、纸墨、霓虹赛博、深林、深海、晚霞、水墨、终端绿、糖果。

- 全局默认主题可在后台设置；每一关还能单独选主题、追加本页自定义 CSS（`data/pages.json`）。
- 主题本身是可编辑的 CSS 源码（`data/themes.json`），后台「主题与背景」里带 CodeMirror 编辑器，可以改内置主题、新建/删除自定义主题、恢复内置默认。
- 主题通过 CSS 变量控制：`--bx-bg`、`--bx-text`、`--bx-accent`、`--bx-field`、`--bx-border`、`--bx-font` 等；公共控件样式在 `themes.py` 的 `BASE_CSS`。
- 背景层是固定定位的 `.bx-bg`，整页覆盖；服务端把 `html/body` 背景强制透明，保证任何自定义页面都能看到背景。

## 文件结构

```
copyspace/stepN.html    第 N 关页面：站长自己的完整 HTML，服务端注入背景和控件
copyspace/其他文件       图片、CSS 等原样返回
data/answers.json       关卡 -> SHA-256 哈希（v2；旧明文文件首次读取会自动迁移）
data/state.json         全网进度、计数、最近猜测（自动维护，1.5 秒合并写盘）
data/pages.json         每关单独的主题/自定义 CSS
data/themes.json        12 个内置主题 + 自定义主题的 CSS 源码
data/settings.json      限流/队列等可在后台修改的设置
data/ipstats.db         每 IP 逐小时请求数（24 窗口，SQLite，120 秒写一次）
data/admin.json         管理密码的 PBKDF2 哈希（设置密码后生成）
data/secret.key         Cookie 签名密钥（自动生成）
app.py                  路由、/game 映射、背景/控件注入、命令行
admin.py                super_admin 后台：登录、答案、页面源码、主题、背景、限流
themes.py               站点设置、主题库、每关背景、CSS 注入
game.py                 哈希答案本、进度、按 IP 限流
ipstats.py              24 小时 IP 统计 + 优先级准入队列
templates/super_admin*.html       后台页面（CodeMirror 5 编辑器）
wsgi.py                 生产入口
```

## 本地运行

```bash
pip install -r requirements.txt
python app.py admin-password   # 第一次：设置管理密码（按提示输两次）
python app.py                  # http://127.0.0.1:5000/ → 自动进入当前关
```

后台：<http://127.0.0.1:5000/super_admin>（公开页面没有入口链接）。

## 关卡页怎么写

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>第 1 关</title>
</head>
<body>
<h3>这里写题面</h3>
<p>这里写提示。</p>
<!-- 可选：写了就把密码框和按钮插在这里；不写就插到 </body> 前 -->
<!--bx-controls-->
</body>
</html>
```

- 页面里不需要任何脚本，也不用自己写密码框；服务端每次请求时注入。
- 背景会自动注入，包括整页手写 HTML；要覆盖背景效果就在后台给这一关写自定义 CSS。
- 控件类名是 `bx-controls`，错误提示是 `.bx-error`。
- 答案不能含空格，最长 64 字符。

## super_admin 后台

第一次使用先设置管理密码：

```bash
python app.py admin-password              # 推荐：交互输入两次，不进 shell 历史
python app.py admin-password 你的密码      # 一步到位，密码至少 8 位
```

登录后：

- **统计与运行状态**：全网当前关卡、总猜测、最近 1 小时、玩家数、24 小时活跃 IP、准入队列、本机访问提示。
- **限流设置**：每 IP 在多少秒内最多请求多少次（默认 5 秒 20 次）、同时处理上限、排队等待上限；保存即生效，不用重启。
- **改每一关答案**：填明文即可，服务器自动算 SHA-256 保存；也可以粘贴 `sha256:<64 位十六进制>`。
- **编辑整页源码**：CodeMirror 5（HTML/CSS/JS 高亮、括号匹配、折叠、搜索、Ctrl+S、全屏；CDN 失败自动回退 textarea）。
- **每关背景**：在编辑源码页下方选主题、写本页 CSS；留空则跟随默认主题。
- **主题与背景**：编辑 12 个内置主题的 CSS 源码、新建/删除自定义主题、设置默认主题、查看每关背景配置。
- **新增 / 更新关卡**、一键切换到任意关、重置全网进度、查看最近猜测（只显示提交的哈希）、修改管理密码。
- 管理密码错误时（非 127.0.0.1）等待 1 秒；登录按 IP 限流。
- 登录后如果近 6 小时有来自 `127.0.0.1` 的访问，会明确提示；近 6 小时只有本机访问时也会提醒检查反向代理和端口暴露。

安全边界：

- 管理密码用 PBKDF2-SHA256 加盐存 `data/admin.json`（POSIX 下 chmod 600），不保存明文。
- 登录态是签名 Cookie + 空闲超时（默认 30 分钟）；所有修改表单带 CSRF token，只接受 POST。
- `/super_admin` 带 `noindex`、`X-Frame-Options: DENY`、`no-store`。
- 玩家限流按 IP，`127.0.0.1` / `::1` 完全放行；管理登录错误延迟 1 秒。
- 管理密码可以用命令行随时重设，服务器不用重启，旧登录会自动失效。

## 限流与优先级队列

- 每个 IP 默认 **5 秒 20 次**（可在后台改）；回环地址不限。
- 进入 `/game/*` 和 `/api/answer` 前要过一道并发闸门：默认同时处理 48 个，超出按优先级排队。
- 优先级 = 该 IP 最近 24 个小时窗口的请求总数，**请求少的 IP 优先放行**，避免少数 IP 堆积把新访客堵死。
- 每 IP 的逐小时请求数在内存中保留 24 个窗口，后台线程每 120 秒写入 `data/ipstats.db`（SQLite，WAL），并清理 24 小时前的行。默认最多跟踪 20000 个 IP，内存有上限。
- 单进程约束：全局进度、限流和队列都在内存里，**只能跑一个 worker**，用多线程提高并发。

## 命令行（备用）

```bash
python app.py status                # 当前关卡、每题哈希前缀、猜测数、页面是否存在、管理密码是否设置
python app.py log [条数]            # 最近猜测（时间、关卡、玩家 id、提交哈希、对错）
python app.py reset                 # 回到第 1 关，计数清零，答案保留
python app.py goto 3                # 把全网进度拨回第 3 关
python app.py set-answer 3 3ff1726c # 改/加第 3 关答案（内部转成 SHA-256）
python app.py hash 文本             # 手动算 SHA-256
python app.py admin-password        # 重设管理密码
python app.py run --host 0.0.0.0 --port 8080
```

直接编辑 `data/answers.json`（v2 格式，只存哈希）也可以，运行时按文件修改时间热加载：

```json
{
  "version": 2,
  "hashes": {
    "1": "fb05e8de46f53f6c5da45a52e4226d9e99709dce2196735ef1c733fe0cc0fc92",
    "2": "2f0958707bae688ea04d8868f7dbd859ade4025b032e200f2867b58bd38d8a45"
  }
}
```

旧版明文 `data/answers.json` 首次读取会自动改写为哈希（原文随即从文件中消失）。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 303 到玩家自己的当前关卡；通关后显示完成提示 |
| GET | `/game/N` | 返回第 N 关（仅当前关；管理员可预览），注入背景 + 控件 |
| GET | `/copyspace/stepN.html` | 302 到 `/game/N` |
| GET | `/copyspace/其他文件` | 原样返回图片、CSS 等 |
| POST | `/api/answer` | 表单 `step` + `hash`（64 位十六进制），成功跳下一关，失败回本关；请求体上限 1 KB |
| GET/POST | `/super_admin/...` | 后台页面与表单（密码保护） |

## 配置

`config.json`，缺省项自动回退：

| 键 | 说明 |
| --- | --- |
| `server.host` / `server.port` / `server.debug` | 监听地址与调试模式 |
| `security.secret_key_file` | Cookie 签名密钥文件 |
| `security.session_cookie_name` | Cookie 名 |
| `security.session_max_age_seconds` | Cookie 有效期 |
| `security.session_secure` | 上 HTTPS 后设为 `true` |
| `security.max_guess_length` | 设置答案时明文的最大长度 |
| `security.answer_max_body_bytes` | 密码 POST 表单上限，默认 1024 |
| `game.answers_file` / `game.state_file` | 答案与状态文件位置 |
| `game.state_flush_seconds` | 状态合并写盘间隔（默认 1.5 秒） |
| `game.settings_file` / `game.themes_file` / `game.pages_file` | 后台设置、主题、每关背景文件 |
| `game.ipstats_file` | IP 逐小时统计 SQLite 文件 |
| `game.copyspace_dir` | 关卡页面目录，默认 `copyspace` |
| `game.log_size` | `state.json` 里保留的最近猜测条数 |
| `game.page_cache_size` | 关卡源码内存缓存条数（另有 32 MB 总字节上限） |
| `game.max_tracked_ips` / `game.ipstats_flush_seconds` | IP 跟踪上限、写库间隔 |
| `admin.credentials_file` | 管理密码哈希文件 |
| `admin.session_max_age_seconds` | 管理会话空闲超时（默认 1800 秒） |
| `admin.hash_iterations` | 管理密码 PBKDF2 迭代次数 |
| `admin.min_password_length` | 管理密码最短长度（默认 8） |
| `admin.login_rate_limit_per_5min` | 管理登录默认每 IP 5 分钟次数 |
| `admin.wrong_password_delay_ms` | 非本机密码错误等待（默认 1000 毫秒） |

运行时可在后台改的限流/队列项在 `data/settings.json`，优先级高于代码默认值。

## 部署

单 worker + 多线程。Web 进程常驻内存约 40–70 MB；IP 统计有上限；SQLite 自动清理 24 小时前的行。

```bash
pip install gunicorn
gunicorn -w 1 --threads 32 --timeout 30 -b 0.0.0.0:8081 wsgi:app
```

`/super_admin` 直连时 `remote_addr` 是玩家真实 IP；如果放在 Nginx 后面，所有请求都会变成 `127.0.0.1`，限流和优先级会退化为单 IP（那时会看到后台的本机访问提示）。需要按真实 IP 限流时，在代理层传真实 IP 并给 Flask 配 `ProxyFix`，或者只暴露 gunicorn 端口。

上线要点：

- `data/` 必须持久化：答案哈希、全网进度、主题、每关背景、管理密码、Cookie 密钥都在里面。删 `state.json` 等于重开第一轮；删 `secret.key` 等于所有玩家进度作废；删 `admin.json` 等于要重新设置管理密码。
- 上 HTTPS 后把 `security.session_secure` 设为 `true`。
- 部署后无需系统服务：`start.sh` 用 nohup 启动，重启机器后手动执行一次。
