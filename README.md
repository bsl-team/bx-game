# bx-game

一个「全网共猜一关」的极简密码迷宫：纯 HTML 页面、没有样式、没有管理界面。结构参考 [maze.001365.xyz](https://maze.001365.xyz)：每一层就是站长自己写的一个网页，页面里只有一个密码框；答案放在服务端，猜对了自动进入下一层。

## 玩法

1. `/` 是入口页：显示你自己和全网的进度，输入当前关卡的密码就能前进，也可以点链接进入关卡页面。
2. 每一关是独立的 `/step/N` 页面，内容就是 `levels/stepN.html` 里的 HTML。页面默认隐藏，`guard.js` 向服务端确认进度后再显示；没解锁的层会被弹回你自己的当前层。
3. 所有玩家共享一轮：任何人猜对当前关卡，全网就开到下一关。新玩家从第 1 关开始，逐层追上来。
4. 密码就是答案，区分大小写，服务端用 `hmac.compare_digest` 比较；玩家接口不会返回答案或哈希。
5. 猜错不扣次数，但按 Cookie 限流（默认每分钟 30 次、两次间隔 150ms）。

> `guard.js` 的隐藏只是前端软门禁：`/step/N` 的源码谁都能直接抓。这是参考站同款的模型，别把答案写进页面——答案只在 `data/answers.json`。

## 文件结构

```
index.html            入口页（纯 HTML，可随意改）
guard.js              关卡页的进度校验脚本，/guard.js 提供
levels/stepN.html     第 N 层页面，站长自己写，服务端原样输出
data/answers.json     关卡 -> 答案（明文，仅服务端读；首次启动自动生成）
data/state.json       全网进度、计数、最近猜测（自动维护）
data/secret.key       Cookie 签名密钥（自动生成）
app.py                路由、API、命令行
game.py               答案本、进度、限流
wsgi.py               生产入口
```

## 本地运行

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000/
```

## 管理：没有管理界面

`/admin` 不存在，也没有登录、Token、CSRF 这些东西。管理就是改文件 + 命令行：

```bash
python app.py status                # 当前关卡、每题答案、猜测数、页面是否存在
python app.py log [条数]            # 最近的猜测记录（时间、关卡、玩家 id、原文、对错）
python app.py reset                 # 回到第 1 关，计数清零，答案保留
python app.py goto 3                # 把全网进度拨回第 3 关（第 3 关及以后标记未通过）
python app.py set-answer 3 3ff1726c # 改/加第 3 关的答案
python app.py run --host 0.0.0.0 --port 8080 --debug
```

- 直接编辑 `data/answers.json` 也可以，运行时按文件修改时间自动热加载：

  ```json
  {
    "1": "219845122340",
    "2": "nbaf",
    "3": "3ff1726c"
  }
  ```

  键就是关卡号，从 1 开始，答案不能含空格，最长 64 字符（`security.max_guess_length`）。
- `set-answer` 只改答案并清掉这一层的「已通过」标记；要让已经过了这关的全网重新猜，再执行 `goto N`。
- `state.json` 的猜测记录里保存玩家提交的原文，`python app.py log` 能看到；不需要就把 `game.log_size` 调小。

### 新增一关

1. 复制 `levels/step5.html` 为 `levels/step6.html`，改标题、把 `MazeGuard.enter(5)` 和 `MazeGuard.submit(5, …)` 里的编号改成 6，题面随便写。
2. 加上第 6 关的答案：`python app.py set-answer 6 答案`，或者直接往 `data/answers.json` 写一条。
3. 不用重启。关卡页写好后立即可访问，答案文件改动即时生效。

关卡页的最小骨架（`guard.js`、`#pwd`、`#msg` 三个约定不能省）：

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>第 6 层</title>
<script src="/guard.js"></script>
<style>html { visibility: hidden; }</style>
</head>
<body>
<script>MazeGuard.enter(6);</script>
<h2>请输入第 6 层密码：</h2>
<input type="password" id="pwd">
<button onclick="nextPwd()">进入下一步</button>
<p id="msg"></p>
<script>
async function nextPwd() {
  await MazeGuard.submit(6, document.getElementById("pwd").value);
}
</script>

<h3>这里写你的题面</h3>
</body>
</html>
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 入口页 |
| GET | `/step/N` | 原样返回 `levels/stepN.html` |
| GET | `/guard.js` | 关卡页进度校验脚本 |
| GET | `/api/progress` | 自己的当前层、全网进度、总猜测数 |
| POST | `/api/answer` | `{ "step": N, "password": "..." }`，对了返回 `next` 或 `finished` |

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
| `game.levels_dir` | 关卡页面目录，默认 `levels` |
| `game.log_size` | `state.json` 里保留的最近猜测条数 |

## 部署

全局进度是「单进程 + 文件锁 + 内存限流」，**只能跑一个 worker**，多进程会互相覆盖 `data/state.json`。

```bash
# Linux
pip install gunicorn
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

- `data/` 必须持久化：`answers.json` 是答案，`state.json` 是全网进度，`secret.key` 是 Cookie 密钥。删 `state.json` 等于重开第一轮；删 `secret.key` 等于所有玩家进度作废。
- 上 HTTPS 后把 `security.session_secure` 设为 `true`。
- Nginx 后面 `remote_addr` 都是 `127.0.0.1`，限流会退化成全局限流；要按真实 IP 做就得配 `ProxyFix` 或把限流值当全局预算来调。
- 从旧版（聊天界面 + `/admin` 面板）升级：旧格式的 `data/state.json` 会在首次启动时自动改名成 `state.json.old`，从第 1 关重新开始；`templates/`、`static/`、`admin.py` 已经删除，旧部署里可以直接清掉。
