# bx-game · 猜密码

一个「全网共猜一个密码」的网页小游戏，Python + Flask 编写。

## 游戏规则

1. 后台用 `secrets` 随机生成一个密码，服务端只保存它的加盐哈希（PBKDF2-HMAC-SHA256），明文不落盘。
2. 前端唯一的提示是**密码长度**，字符范围不透露。
3. 任意玩家猜对，当前关卡即结束，服务端自动生成新密码并进入下一关。
4. `全网总猜测` 是所有玩家共享的累计次数，本关次数单独统计。
5. 过关进度写进签名 Cookie：**第一关没过，服务端就不会把你的请求判定到第二关**，即使你直接猜第二关的密码也算错。
6. 密码长度按关卡递增（`level1_length` + `length_step`，上限 `length_max`）。

## 启动

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000/
```

其他命令：

```bash
python app.py status     # 查看当前关卡与全局计数
python app.py reset      # 清空进度，回到第一关
python app.py run --host 0.0.0.0 --port 8080 --debug
```

## 配置

全部细粒度配置在 `config.json`，缺省项会自动回退到内置默认值。

| 分组 | 键 | 说明 |
| --- | --- | --- |
| `server` | `host` / `port` / `debug` | 监听地址与调试模式 |
| `security` | `hash_algorithm` | `pbkdf2_sha256`、`pbkdf2_sha512`、`sha256` 等 |
| `security` | `hash_iterations` | PBKDF2 迭代次数 |
| `security` | `secret_key_file` | Cookie 签名密钥文件，删掉即让所有玩家进度失效 |
| `security` | `session_max_age_seconds` | Cookie 有效期 |
| `security` | `guess_rate_limit_per_minute` | 每个 Cookie 每分钟可猜次数 |
| `security` | `guess_min_interval_ms` | 两次猜测的最小间隔 |
| `password` | `charset` | `digits` / `lower` / `upper` / `hex` / `alnum_lower` / `alnum` |
| `password` | `level1_length` / `length_step` / `length_max` | 关卡长度曲线，即前端唯一提示 |
| `password` | `avoid_repeats` | 禁止相邻重复字符 |
| `rounds` | `max_levels` | 关卡总数，`0` 表示无限 |
| `rounds` | `auto_advance` | 猜对后是否自动开下一关 |
| `rounds` | `state_file` | 全局状态落盘位置 |
| `ui` | `title` / `subtitle` / `footer_text` | 页面文案 |
| `ui` | `accent_color` | 主题色，默认 ChatGPT 绿 `#10a37f` |
| `ui` | `poll_interval_ms` | 前端拉取全局进度的间隔 |
| `ui` | `show_charset_hint` | 是否额外提示字符范围（默认关闭） |

## 说明

- 全局状态在 `data/state.json`，`data/` 已在 `.gitignore` 中；单机多线程用文件锁 + 临时文件原子替换写入。
- 关卡密码记录只含 `salt` / `hash` / `length` / 猜测次数，接口不会返回任何哈希字段。
- 界面为 ChatGPT 风格：中性灰背景、圆角气泡、底部胶囊输入框 + 绿色圆形发送按钮，深色模式跟随系统。
- 限流按 Cookie 计数，超限返回 `429`。
