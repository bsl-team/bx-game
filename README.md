# bx-game · 猜密码

一个「全网共猜一关」的解谜闯关站，Python + Flask 编写。玩法参考了逐层密码迷宫：每关一道题，答案就是密码，猜对自动进下一层。

## 游戏规则

1. 每一关有题面（`data/questions.json` 的第 i 题 = 第 i 关），答案就是这一关的密码；服务端只保存答案的加盐哈希，校验用 `hmac.compare_digest`。
2. 前端把题面、答案长度显示出来，字符范围不透露；题面提示（`hint`）要玩家点「看提示」才出现，可以整体关掉（`ui.show_hint`）。
3. 任意玩家猜对，当前关卡即结束，服务端自动开启下一关并绑定下一题。
4. `全网总猜测` 是所有玩家共享的累计次数，本关次数单独统计。
5. 过关进度写进签名 Cookie：**第一关没过就打不开第二关**。每关是独立页面 `/level/N`（可以是站长自己写的整页 HTML，见「关卡内容怎么配」），未解锁的页面会被服务端弹回你自己的关卡，`/api/level/N` 也直接返回 403——锁着的关卡连题面都不会下发。
6. `/level/1` 这类已通过的关卡可以回看题面，但输入框锁定，不能再猜。
7. 不需要题目的玩法也能切：`rounds.mode` 设为 `random` 就回到「纯随机密码 + 只给长度」，题目不够用时对应关卡也自动退回随机密码。

## 本地运行

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000/
```

运维命令：

```bash
python app.py status       # 查看当前关卡与全局计数
python app.py reset        # 清空进度，回到第一关
python app.py admin-token  # 取回首次设置用的 admin token
python app.py admin-reset  # 忘记管理密码：作废旧密码，重新发放 token
python app.py run --config /path/to/config.json --host 0.0.0.0 --port 8080 --debug
```

## 关卡内容怎么配

题库 `data/questions.json` 是一个数组，第 i 项就是第 i 关，字段：

```json
[
  {
    "prompt": "纯文本题面（聊天页显示，也是源码页的兜底说明）",
    "answer": "219845122340",
    "hint": "点「看提示」才显示",
    "title": "浏览器标签标题（可选）",
    "html": "<!doctype html>…<!--bx-password-->…"
  }
]
```

- 不填 `html`：这一关用内置的聊天页（题面卡 + 提示按钮 + 猜测记录 + 关卡导航）。
- 填了 `html`：**整页源码就是你写的**，服务端原样输出，只在 `<!--bx-password-->` 处替换成密码框；没有锚点就插到 `</body>` 前面，纯片段就追加到末尾。你页面里自己的 HTML/CSS/JS 一律不动。
- 密码框自带「第几关 / 答案长度 / 全网已猜 / 本关已猜」，猜对自动跳下一关；这关已经过去了就自动锁定输入框并给一个「回到当前关卡」的链接。想改外观就覆盖 `static/widget.css` 里 `.bxw` 的变量。
- 关卡顺序 = 数组顺序；文件不存在时用内置 5 道题起步（面板一改就写盘）。答案不能含空格，最长 64 字符。
- 面板改答案 = 该关重新开放；删题 = 后面的题前移，多出来的关卡转成随机密码。

> 信任边界：`html` 是原样输出的，等于把这一关页面的完全控制权交给站长账号——只有管理员能写，所以管理密码的安全级别就是整站的脚本权限。别把面板账号给别人，上线务必走 HTTPS 并开 `security.session_secure`。

## 管理面板

地址 `/admin`（已加 `noindex`，玩家页面不提供入口链接）。

**首次进入用 token 设置密码**：服务启动时会在控制台打印 admin token，同时写在 `data/admin_token.txt`。在面板输入 token + 新密码（默认至少 8 位）即可，设置成功后 token 立即作废、`admin_token.txt` 自动删除，之后一律用密码登录。忘记密码就执行 `python app.py admin-reset` 重新发 token——它会顺带踢掉所有已登录的管理会话。

面板能做的事：

- 看每一关的**明文答案**、题面、长度、来源（题库/随机/管理员自定义）、本关被猜次数、是否已被破解、破解时间和玩家短 id。
- 看全网总猜测次数、最近 1 小时次数、已解开关卡数、日志里出现过的玩家数、当前模式和题库题数、状态文件大小。
- 看「最近尝试」日志：时间、关卡、玩家 id、提交原文、长度、是否命中。
- **管题库**：新增/编辑/删除题目（题面、答案、提示、页面标题、**整页网页源码 HTML**），有源码时列表会标「源码 N 字符」、关卡行标「网页源码」，还有「预览这一关」直接打开 `/level/N`。改了答案会自动重绑对应关卡并把该关重新开放，删了题目后面的题往前移、多出来的关卡换成随机密码。
- 改任意一关的答案（自填或按字符集+长度随机重生成）、按题库重新绑定某关、新增关卡、把当前关卡跳到任意一关、清零本关或全网计数。
- 修改管理密码、退出登录。

安全边界：

- 管理接口全部要求登录后的签名 Cookie，并且 POST 必须带匹配的 CSRF token；登录/改密按 IP 限流（默认 5 分钟 10 次）。
- 管理会话有滑动空闲超时（`admin.session_max_age_seconds`），改密码会立刻让旧会话失效。
- 明文密码写在 `data/state.json`（管理员要看它就得落盘）；把 `admin.reveal_passwords` 设为 `false` 会在启动时抹掉已存明文并停止记录，面板只显示长度。`data/` 已 gitignore，权限请按机密文件对待（POSIX 下 `admin.json`/`admin_token.txt` 会 chmod 600）。
- 尝试日志默认保留玩家提交的原文（`admin.log_guess_text`），有人可能在这里输入别的口令；不需要就关掉，或把 `admin.guess_log_size` 调小。
- 玩家侧 `/api/status` 与 `/api/guess` 永远不会返回密码或哈希字段。

## 部署方式

全局状态是「单进程 + 文件锁 + 内存限流」实现的，**只能跑一个 worker**，多进程会互相覆盖 `data/state.json` 并各自计数。

```bash
# Linux（推荐 gunicorn，线程并发足够扛小规模流量）
pip install gunicorn
gunicorn -w 1 --threads 16 -b 127.0.0.1:8080 wsgi:app

# Windows 本机常驻
pip install waitress
waitress-serve --listen=127.0.0.1:8080 wsgi:app
```

Nginx 反代 + systemd：

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

```ini
# /etc/systemd/system/bx-game.service
[Service]
WorkingDirectory=/opt/bx-game
ExecStart=/usr/bin/gunicorn -w 1 --threads 16 -b 127.0.0.1:8080 wsgi:app
Restart=always
[Install]
WantedBy=multi-user.target
```

上线要点：

- `data/` 必须持久化：`state.json` 是关卡进度（含面板要看的明文答案），`questions.json` 是题库，`secret.key` 是 Cookie 签名密钥，`admin.json` 是管理密码哈希；换机器要一起备份。删 `secret.key` 等于所有人进度作废，删 `state.json` 等于重开第一关，删 `admin.json` 等于重新发 admin token。
- 上了 HTTPS 就把 `config.json` 的 `security.session_secure` 设为 `true`，否则管理 Cookie 会走明文 HTTP。
- Nginx 后面 `request.remote_addr` 是 `127.0.0.1`，登录限流会退化成「全局限流」；要么按 `X-Forwarded-For` 做 `--forwarded-allow-ips`（gunicorn `--proxy-protocol` / `ProxyFix`），要么把 `admin.login_rate_limit_per_5min` 当作全局预算来调。
- 公网部署请调大 `hash_iterations` 或加长 `level1_length`，并把 `guess_rate_limit_per_minute` 压到个位数。

## 配置

全部细粒度配置在 `config.json`，缺省项会自动回退到内置默认值。

| 分组 | 键 | 说明 |
| --- | --- | --- |
| `server` | `host` / `port` / `debug` | 监听地址与调试模式 |
| `security` | `hash_algorithm` | `pbkdf2_sha256`、`pbkdf2_sha512`、`sha256` 等 |
| `security` | `hash_iterations` | PBKDF2 迭代次数 |
| `security` | `secret_key_file` | Cookie 签名密钥文件 |
| `security` | `session_max_age_seconds` | Cookie 有效期 |
| `security` | `session_secure` | 仅 HTTPS 下发送 Cookie |
| `security` | `guess_rate_limit_per_minute` | 每个 Cookie 每分钟可猜次数 |
| `security` | `guess_min_interval_ms` | 两次猜测的最小间隔 |
| `password` | `charset` | `digits` / `lower` / `upper` / `hex` / `alnum_lower` / `alnum` |
| `password` | `level1_length` / `length_step` / `length_max` | 关卡长度曲线，即前端唯一提示 |
| `password` | `avoid_repeats` | 禁止相邻重复字符 |
| `rounds` | `mode` | `puzzle`（题库闯关，默认）/ `random`（纯随机密码） |
| `rounds` | `questions_file` | 题库位置，默认 `data/questions.json`；文件不存在时用内置 5 道题 |
| `rounds` | `max_levels` | 关卡总数，`0` 表示无限 |
| `rounds` | `auto_advance` | 猜对后是否自动开下一关 |
| `rounds` | `auto_redirect` | 猜对后前端是否自动跳到 `/level/N` 页面 |
| `rounds` | `state_file` | 全局状态落盘位置 |
| `admin` | `credentials_file` / `token_file` | 管理凭据与首次 token 的存放位置 |
| `admin` | `min_password_length` | 管理密码最短长度（默认 8） |
| `admin` | `hash_iterations` | 管理密码 PBKDF2 迭代次数 |
| `admin` | `session_max_age_seconds` | 管理会话空闲超时（默认 30 分钟） |
| `admin` | `login_rate_limit_per_5min` / `login_min_interval_ms` | 登录/初始化按 IP 限流 |
| `admin` | `reveal_passwords` | `false` = 面板不显示明文、启动时清空已存明文 |
| `admin` | `log_guess_text` / `guess_log_size` | 是否记录玩家提交原文、日志保留条数 |
| `ui` | `title` / `subtitle` / `footer_text` | 页面文案 |
| `ui` | `accent_color` | 主题色，默认 ChatGPT 绿 `#10a37f` |
| `ui` | `poll_interval_ms` | 前端拉取全局进度的间隔 |
| `ui` | `show_charset_hint` | 是否额外提示字符范围（默认关闭） |
| `ui` | `show_hint` | 题面提示按钮是否可用（默认开启） |

## 说明

- 路由：`/` 自己的当前关，`/level/N` 第 N 关页面（未解锁会被 302 弹回），`/api/status`、`/api/level/N`、`/api/progress` 玩家侧只读，`/api/guess` 提交答案，`/admin` 管理面板。
- 文件结构：`app.py` 玩家路由与 Cookie 门禁，`admin.py` 管理面板与鉴权，`game.py` 题库/密码生成/哈希/关卡状态，`wsgi.py` 生产入口，`templates/` + `static/` 前端（玩家页与管理页共用 `style.css` 的配色变量）。
- 玩家接口 `/api/status`、`/api/guess`、`/api/level/N` 只返回题面、提示、长度和计数，不含 `salt` / `hash` / 明文答案；明文答案只在 `/admin/api/overview` 里出现，且需要登录 + CSRF。
- `data/questions.json` 不存在时用代码里内置的 5 道题起步；面板一改就会写盘。
- 写盘用临时文件 + `os.replace` 原子替换，避免中途崩溃留下半个 JSON。
- 界面为 ChatGPT 风格：中性灰背景、圆角气泡、底部胶囊输入框 + 绿色圆形发送按钮，深色模式跟随系统。
- 限流按 Cookie 计数，超限返回 `429`；含空格或超长的输入直接被拒且不计入全局次数。
