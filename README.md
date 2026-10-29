# bx-game · 猜密码

一个「全网共猜一个密码」的网页小游戏，Python + Flask 编写。

## 游戏规则

1. 后台用 `secrets` 随机生成一个密码，校验走加盐哈希（PBKDF2-HMAC-SHA256），比对用 `hmac.compare_digest`；玩家接口只见到长度。
2. 前端唯一的提示是**密码长度**，字符范围不透露。
3. 任意玩家猜对，当前关卡即结束，服务端自动生成新密码并进入下一关。
4. `全网总猜测` 是所有玩家共享的累计次数，本关次数单独统计。
5. 过关进度写进签名 Cookie：**第一关没过，服务端就不会把你的请求判定到第二关**，即使你直接猜第二关的密码也算错。
6. 密码长度按关卡递增（`level1_length` + `length_step`，上限 `length_max`）。

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

## 管理面板

地址 `/admin`（已加 `noindex`，玩家页面不提供入口链接）。

**首次进入用 token 设置密码**：服务启动时会在控制台打印 admin token，同时写在 `data/admin_token.txt`。在面板输入 token + 新密码（默认至少 8 位）即可，设置成功后 token 立即作废、`admin_token.txt` 自动删除，之后一律用密码登录。忘记密码就执行 `python app.py admin-reset` 重新发 token——它会顺带踢掉所有已登录的管理会话。

面板能做的事：

- 看每一关的**明文密码**、长度、字符集、本关被猜次数、是否已被破解、破解时间和玩家短 id。
- 看全网总猜测次数、最近 1 小时次数、已解开关卡数、日志里出现过的玩家数、状态文件大小。
- 看「最近尝试」日志：时间、关卡、玩家 id、提交原文、长度、是否命中。
- 改任意一关的密码（自填明文或按字符集+长度随机重生成），改完该关自动重新开放。
- 新增关卡（可指定数量/长度/字符集/明文密码）、把当前关卡跳到任意一关、清零本关或全网计数。
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

- `data/` 必须持久化：`state.json` 是关卡进度（含面板要看的明文密码），`secret.key` 是 Cookie 签名密钥，`admin.json` 是管理密码哈希；换机器要一起备份。删 `secret.key` 等于所有人进度作废，删 `state.json` 等于重开第一关，删 `admin.json` 等于重新发 admin token。
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
| `rounds` | `max_levels` | 关卡总数，`0` 表示无限 |
| `rounds` | `auto_advance` | 猜对后是否自动开下一关 |
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

## 说明

- 文件结构：`app.py` 玩家路由与 Cookie 门禁，`admin.py` 管理面板与鉴权，`game.py` 密码生成/哈希/关卡状态，`wsgi.py` 生产入口，`templates/` + `static/` 前端（玩家页与管理页共用 `style.css` 的配色变量）。
- 玩家接口 `/api/status`、`/api/guess` 只返回长度和计数，不含 `salt` / `hash` / 明文；明文密码只在 `/admin/api/overview` 里出现，且需要登录 + CSRF。
- 写盘用临时文件 + `os.replace` 原子替换，避免中途崩溃留下半个 JSON。
- 界面为 ChatGPT 风格：中性灰背景、圆角气泡、底部胶囊输入框 + 绿色圆形发送按钮，深色模式跟随系统。
- 限流按 Cookie 计数，超限返回 `429`；含空格或超长的输入直接被拒且不计入全局次数。
