# bx-game

一个没有主页的密码迷宫。访问 `/` 会自动跳到玩家自己的当前关卡；每一关完全是站长自己写的 HTML，服务端只在页面里插入一个密码框和「上一关 / 下一关」两个普通按钮。答案在服务端，管理后台在 `/admin`。

## 玩法

1. 打开 `/` → 302 跳到自己的当前关卡 `/copyspace/stepN.html`；全部通关后 `/` 显示「全部关卡已通过。」。
2. 关卡页就是 `copyspace/stepN.html` 里的完整 HTML，随便写题面、样式、图片。服务端会在 `</body>` 前插入：

   ```html
   <form class="bx-controls" method="post" action="/api/answer">
     <input type="hidden" name="step" value="N">
     <input type="password" name="password" placeholder="密码" required>
     <button type="button" onclick="...">上一关</button>
     <button type="submit">下一关</button>
   </form>
   ```

   - **下一关**：提交密码。答对自动跳到下一关；答错回到本关，控件上方显示「密码不对。」。
   - **上一关**：回到上一关页面；第 1 关时按钮禁用。
   - 想指定控件位置，就在页面里写 `<!--bx-controls-->`；不写就插到 `</body>` 前。控件没有自带样式，可以用 `.bx-controls` 自己写 CSS。
3. 所有玩家共享一轮：任何人答对当前关卡，全网就开到下一关；玩家按 Cookie 从第 1 关逐层追上来。
4. 未解锁的关卡 URL 会被服务端弹回你的当前关卡；管理登录状态下可以预览任意关卡。
5. 猜错不扣次数，但按 Cookie 限流（默认每分钟 30 次、两次间隔 150ms）。答案只在 `data/answers.json`，页面里没有。

## 文件结构

```
copyspace/stepN.html    第 N 层页面：站长自己的完整 HTML，服务端注入控件
copyspace/其他文件       图片、CSS 等原样返回
data/answers.json       关卡 -> 答案（明文，仅服务端读；首次启动自动生成）
data/state.json         全网进度、计数、最近猜测（自动维护）
data/admin.json         管理密码的 PBKDF2 哈希（设置密码后生成）
data/secret.key         Cookie 签名密钥（自动生成）
app.py                  路由、控件注入、命令行
admin.py                管理后台：登录、改答案、生成关卡页
templates/admin.html    管理后台的唯一页面
game.py                 答案本、进度、限流
wsgi.py                 生产入口
```

## 本地运行

```bash
pip install -r requirements.txt
python app.py admin-password   # 第一次：设置管理密码（按提示输两次）
python app.py                  # 打开 http://127.0.0.1:5000/ 会自动进入第 1 关
```

管理后台：<http://127.0.0.1:5000/admin>（公开页面里没有入口链接）。

## 关卡页怎么写

最小示例：

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
- 控件的 HTML 类名是 `bx-controls`，错误提示是里面的 `<p>`，可以按需加样式。
- `copyspace/` 下的非 `stepN.html` 文件（图片、CSS、JS）会原样返回，路径照常引用。
- 答案不能含空格，最长 64 字符。

## 管理界面 /admin

第一次使用先设置管理密码：

```bash
python app.py admin-password              # 推荐：交互输入两次，不进 shell 历史
python app.py admin-password 你的密码      # 一步到位，密码至少 8 位
```

登录后能做的事：

- 看全网当前关卡、总关卡、总猜测、最近 1 小时猜测、记录中的玩家数。
- **改每一关的答案**：表格里直接改、点保存，这一关立刻重新开放。
- **新增 / 更新关卡**：填关卡号、答案、页面标题和题面 HTML，勾选「生成关卡页」，后台会写出完整的 `copyspace/stepN.html`。密码框和「上一关 / 下一关」由服务器自动插入，不用写；想指定位置就在题面里写 `<!--bx-controls-->`。
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
- 管理登录状态下可以预览未解锁关卡；普通玩家访问未来关卡会被弹回自己的当前关。
- 管理密码可以用命令行随时重设，服务器不用重启，旧登录会自动失效。

## 命令行（备用）

```bash
python app.py status                # 当前关卡、每题答案、猜测数、页面是否存在、管理密码是否设置
python app.py log [条数]            # 最近的猜测记录（时间、关卡、玩家 id、原文、对错）
python app.py reset                 # 回到第 1 关，计数清零，答案保留
python app.py goto 3                # 把全网进度拨回第 3 关（第 3 关及以后标记未通过）
python app.py set-answer 3 3ff1726c # 改/加第 3 关的答案
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

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 302 到玩家自己的当前关卡；通关后显示完成提示 |
| GET | `/copyspace/stepN.html` | 返回关卡页，并注入密码框 + 上一关 / 下一关 |
| GET | `/copyspace/其他文件` | 原样返回图片、CSS 等 |
| POST | `/api/answer` | 表单提交 `step` + `password`，成功跳下一关，失败回本关 |
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
- 旧版（首页快捷跳转 + `hash/` + `guard.js` + `/api/progress`）已经全部删掉；旧格式的 `data/state.json` 首次启动会自动改名成 `state.json.old`，从第 1 关重新开始。旧格式的 `data/admin.json` 会被忽略，用 `python app.py admin-password` 重新设置即可。
