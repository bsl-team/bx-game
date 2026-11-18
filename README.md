# bx-game

一个和 [maze.001365.xyz](https://maze.001365.xyz) 一模一样的极简密码迷宫：公开站点纯 HTML、没有样式；另有一个独立的管理后台 `/admin`，密码登录，用表单改答案、生成关卡页。

## 玩法

1. 首页 `index.html` 只有一个快捷跳转：

   ```text
   Hello world!
   这里是密码迷宫
   [快捷跳转        ] [完成]
   密码迷宫:114514
   第二层:1919810
   最后一层:1145141919810
   ```

   输入列表里的入口码，`hashStrong`（明文 SHA-256）命中后跳到对应页面。

2. 每一层是独立的 `/copyspace/stepN.html`。页面默认隐藏，`CopyspaceGuard.enter(N)` 向 `/api/progress` 确认进度后才显示；没解锁的层会被弹回你自己的当前层。
3. 所有玩家共享一轮：任何人猜对当前层，全网就开到下一层；玩家各自按 Cookie 从第 1 层逐层追上来。
4. 答案只在服务端 `data/answers.json`，页面里没有答案；入口码（114514 / 1919810 / 1145141919810）和关卡答案无关。
5. 猜错不扣次数，但按 Cookie 限流（默认每分钟 30 次、两次间隔 150ms）。

> `CopyspaceGuard` 的隐藏只是前端软门禁：`/copyspace/stepN.html` 的源码谁都能直接抓，这是参考站同款的模型。别把答案写进页面。

## 文件结构

```
index.html              首页：hashStrong 快捷跳转
hash/hashp.js           hashStrong = SHA-256 十六进制
copyspace/stepN.html    第 N 层页面，服务端原样输出
copyspace/guard.js      CopyspaceGuard：藏页面、查进度、提交答案
data/answers.json       关卡 -> 答案（明文，仅服务端读；首次启动自动生成）
data/state.json         全网进度、计数、最近猜测（自动维护）
data/admin.json         管理密码的 PBKDF2 哈希（首次设置密码后生成）
data/secret.key         Cookie 签名密钥（自动生成）
app.py                  路由、API、命令行
admin.py                管理后台：登录、改答案、生成关卡页
templates/admin.html    管理后台的唯一页面
game.py                 答案本、进度、限流
wsgi.py                 生产入口
```

## 本地运行

```bash
pip install -r requirements.txt
python app.py admin-password   # 第一次：设置管理密码（按提示输两次）
python app.py                  # http://127.0.0.1:5000/
```

管理后台：<http://127.0.0.1:5000/admin>（公开页面里没有入口链接）。

## 管理界面 /admin

第一次使用先设置管理密码，二选一：

```bash
python app.py admin-password              # 推荐：交互输入两次，不进 shell 历史
python app.py admin-password 你的密码      # 一步到位，密码至少 8 位
```

登录后能做的事：

- 看全网当前关卡、总关卡、总猜测、最近 1 小时猜测、记录中的玩家数。
- **改每一关的答案**：表格里直接改、点保存，这一关立刻重新开放。
- **新增 / 更新关卡**：填关卡号、答案、页面标题和题面 HTML，勾选「生成关卡页」，后台会写出一个和示例一样的 `copyspace/stepN.html`（自带 `CopyspaceGuard.enter/submit` 和隐藏样式），不用手动碰代码。
  - 已存在的页面默认不覆盖；要重写就勾「覆盖已有页面」。
  - 只改答案、保留手写页面时，用关卡表格里的「保存」，不要勾生成页面。
- 一键把全网切到任意关卡；重置全网进度（回到第 1 关，计数清零，答案保留）。
- 看最近 30 条猜测：时间、关卡、玩家短 id、提交原文、对错。
- 修改管理密码（需要当前密码）；修改后所有管理登录立即失效。

安全边界：

- 密码用 PBKDF2-SHA256 加盐存 `data/admin.json`（POSIX 下 chmod 600），不保存明文。
- 登录态是签名 Cookie + 空闲超时（默认 30 分钟）；所有修改表单都带 CSRF token，只接受 POST。
- 登录按 IP 限流（默认 5 分钟 10 次）；改密码会递增凭据版本，旧会话立刻失效。
- `/admin` 带 `noindex`、`X-Frame-Options: DENY`、`no-store`，公开页面不提供入口。
- 管理密码可以用命令行随时重设，服务器不用重启，旧登录会自动失效。
- 首页的快捷跳转码仍然写在 `index.html` 里；要增加入口码，用 `python app.py hash 新入口码` 算出 SHA-256 再加一个 `if`。

## 命令行（备用）

```bash
python app.py status                # 当前关卡、每题答案、猜测数、页面是否存在、管理密码是否设置
python app.py log [条数]            # 最近的猜测记录（时间、关卡、玩家 id、原文、对错）
python app.py reset                 # 回到第 1 关，计数清零，答案保留
python app.py goto 3                # 把全网进度拨回第 3 关（第 3 关及以后标记未通过）
python app.py set-answer 3 3ff1726c # 改/加第 3 关的答案
python app.py hash 我的入口码       # 算 hashStrong，改首页快捷跳转时用
python app.py admin-password        # 重设管理密码
python app.py run --host 0.0.0.0 --port 8080 --debug
```

直接编辑 `data/answers.json` 也可以，运行时按文件修改时间自动热加载：

```json
{
  "1": "219845122340",
  "2": "nbaf",
  "3": "3ff1726c"
}
```

键就是关卡号，从 1 开始，答案不能含空格，最长 64 字符（`security.max_guess_length`）。

### 关卡页的最小骨架

后台生成的页面就是这个结构；手写时 `guard.js`、`#pwd`、`#msg` 三个约定不能省：

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Step 6</title>
<script src="../hash/hashp.js"></script>
<style>html { visibility: hidden; }</style>
<script src="guard.js"></script>
</head>
<body>
<script>CopyspaceGuard.enter(6);</script>
<h2>请输入第 6 层密码：</h2>
<input type="password" id="pwd">
<button onclick="next()">进入下一步</button>
<p id="msg"></p>

<script>
async function next() {
  const pwd = document.getElementById("pwd").value;
  await CopyspaceGuard.submit(6, pwd);
}
</script>

<h3>这里写你的题面</h3>
</body>
</html>
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 首页快捷跳转 |
| GET | `/hash/hashp.js` | hashStrong |
| GET | `/copyspace/stepN.html` | 原样返回关卡页 |
| GET | `/copyspace/guard.js` | CopyspaceGuard |
| GET | `/api/progress` | 自己的当前层、全网进度、总猜测数 |
| POST | `/api/answer` | `{ "step": N, "password": "..." }`，对了返回 `next` 或 `finished` |
| GET/POST | `/admin/...` | 管理后台页面与表单（密码保护） |

## 配置

`config.json`，缺省项自动回退：

| 键 | 说明 |
| --- | --- |
| `server.host` / `server.port` / `server.debug` | 监听地址与调试模式 |
| `security.secret_key_file` | Cookie 签名密钥文件 |
| `security.session_cookie_name` | Cookie 名 |
| `security.session_max_age_seconds` | Cookie 有效期 |
| `security.session_secure` | 上 HTTPS 后设为 `true` |
| `security.guess_rate_limit_per_minute` | 每个 Cookie 每分钟可猜次数 |
| `security.guess_min_interval_ms` | 两次猜测的最小间隔 |
| `security.max_guess_length` | 答案/输入最大长度 |
| `game.answers_file` / `game.state_file` | 答案与状态文件位置 |
| `game.copyspace_dir` | 关卡页面目录，默认 `copyspace` |
| `game.log_size` | `state.json` 里保留的最近猜测条数 |
| `admin.credentials_file` | 管理密码哈希文件 |
| `admin.session_max_age_seconds` | 管理会话空闲超时（默认 1800 秒） |
| `admin.hash_iterations` | 管理密码 PBKDF2 迭代次数 |
| `admin.min_password_length` | 管理密码最短长度（默认 8） |
| `admin.login_rate_limit_per_5min` / `admin.login_min_interval_ms` | 登录按 IP 限流 |

## 部署

全局进度是「单进程 + 文件锁 + 内存限流」，**只能跑一个 worker**，多进程会互相覆盖 `data/state.json`。

```bash
# Linux
pip install gunicorn
python app.py admin-password        # 先设好管理密码
gunicorn -w 1 --threads 16 -b 127.0.0.1:8080 wsgi:app

# Windows
pip install waitress
waitress-serve --listen=127.0.0.1:8080 wsgi:app
```

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

上线要点：

- `data/` 必须持久化：`answers.json` 是答案，`state.json` 是全网进度，`admin.json` 是管理密码哈希，`secret.key` 是 Cookie 密钥。删 `state.json` 等于重开第一轮；删 `secret.key` 等于所有玩家进度作废；删 `admin.json` 等于要重新设置管理密码。
- 上 HTTPS 后把 `security.session_secure` 设为 `true`。
- Nginx 后面 `remote_addr` 都是 `127.0.0.1`，玩家限流和管理登录限流都会退化成全局限流；要按真实 IP 做就得配 `ProxyFix` 或把限流值当全局预算来调。
- 旧版（聊天界面 + 旧 `/admin` 面板 + `/step/N` 路径）已经全部删掉；旧格式的 `data/state.json` 首次启动会自动改名成 `state.json.old`，从第 1 关重新开始。旧格式的 `data/admin.json` 会被忽略，用 `python app.py admin-password` 重新设置即可。
