# HDU Library Seat Booker

杭电图书馆自习室预约工具。项目提供命令行脚本和本地网页界面，主要用于自习室座位预约，默认房间是 `宋韵云图（四楼）`。

> 请只用于本人账号和合理自习需求。不要提交验证码绕过、批量占座、出售座位等用途。

## 功能

- 本地网页管理 `booking.json`
- 查询自习室座位显示号和实际 `seat_id`
- 按候选座位优先级预约
- 手动登录一次后复用 `cookies.json`
- Windows 本地每日定时运行
- GitHub Actions 定时运行
- 查看并取消未开始/使用中的预约

## 环境要求

- Windows 10/11
- Python 3.10+
- Google Chrome
- Python 依赖：

```powershell
pip install -r requirements.txt
```

如果没有 `requirements.txt`，也可以手动安装：

```powershell
pip install requests pyyaml selenium
```

## 下载项目

```powershell
git clone https://github.com/yuan791014094/hdu-seat-booker.git
cd hdu-seat-booker
```

如果你是直接下载 ZIP，解压后在项目目录打开 PowerShell。

## 首次配置

复制模板文件：

```powershell
copy config.example.yaml config.yaml
copy booking.example.json booking.json
```

编辑 `config.yaml`，填写杭电账号密码：

```yaml
account:
  username: "你的学号"
  password: "你的密码"
```

推荐保持：

```yaml
booking:
  date: "auto"

settings:
  trigger_time: "20:00"
```

`date: "auto"` 的规则：

- 晚上 20:00 前运行：预约明天
- 晚上 20:00 后运行：预约后天

## 配置候选座位

编辑 `booking.json`：

```json
{
  "booking": {
    "date": "auto",
    "begin_hour": 12,
    "duration_hours": 10
  },
  "seats_priority": [
    {
      "room_name": "宋韵云图（四楼）",
      "seat_num": "96"
    }
  ]
}
```

字段说明：

- `room_name`：自习室房间名，例如 `宋韵云图（四楼）`
- `seat_num`：平面图上显示的座位号，例如 `96`
- `begin_hour`：开始小时，只支持整数小时，例如 `8`、`12`
- `duration_hours`：预约时长；留空时自动约到 22:00
- `seats_priority`：候选座位优先级，从上到下依次尝试

## 本地网页使用

推荐用启动器打开网页：

```powershell
python start_web.py
```

启动器会自动启动本地服务并打开浏览器。也可以在 Windows PowerShell 里运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\open_web.ps1
```

浏览器地址：

```text
http://127.0.0.1:8765
```

网页里常用按钮：

- `保存 booking.json`：保存候选座位配置
- `验证座位配置`：确认 `room_name + seat_num` 能解析到真实 `seat_id`
- `立即按 JSON 预约`：立刻按 `booking.json` 尝试预约
- `读取座位和平面图`：查看座位显示号和实际 `seat_id`
- `安装每日自动抢座`：写入 Windows 任务计划

如果你只想启动服务、不自动打开浏览器，也可以运行：

```powershell
python web_app.py
```

## 首次登录

第一次运行通常需要手动登录一次：

```powershell
python book.py --show-browser --dry-run
```

登录成功后会生成 `cookies.json`。之后脚本会优先复用 cookie，不需要每次输入账号密码。

如果 cookie 过期，重新运行上面的命令即可。

## 命令行用法

验证配置，不真正预约：

```powershell
python book.py --dry-run
```

立即预约：

```powershell
python book.py --now
```

查看已有预约：

```powershell
python book.py --list-bookings
```

取消指定预约：

```powershell
python book.py --cancel-booking 预约ID
```

导出座位清单：

```powershell
python query_seats.py --print
```

## Windows 每日定时

网页中点击 `安装每日自动抢座`，会创建 Windows 任务计划，默认每天 `config.yaml` 的 `settings.trigger_time` 运行。

也可以手动运行：

先运行 `python start_web.py` 打开本地网页，然后在网页里管理定时任务。

注意：

- 电脑需要开机
- 网络需要正常
- 账号 cookie 不能过期
- 如果学校系统要求验证码，需要手动登录刷新 cookie

## GitHub Actions 部署

仓库自带 `.github/workflows/book.yml`，默认北京时间 20:00 运行。

不要把真实的 `config.yaml`、`booking.json`、`cookies.json` 上传到 GitHub。

在 GitHub 仓库页面进入：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

添加三个 Secrets：

- `HDU_USERNAME`：杭电账号
- `HDU_PASSWORD`：杭电密码
- `BOOKING_JSON`：完整的 `booking.json` 内容

`BOOKING_JSON` 示例：

```json
{
  "booking": {
    "date": "auto",
    "begin_hour": 12,
    "duration_hours": 10
  },
  "seats_priority": [
    {
      "room_name": "宋韵云图（四楼）",
      "seat_num": "96"
    }
  ]
}
```

配置后可以在 GitHub Actions 页面手动点 `Run workflow` 测试一次。

注意：如果学校登录出现验证码，GitHub Actions 无法人工输入验证码，建议使用本地 Windows 定时方式。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `book.py` | 预约主程序 |
| `web_app.py` | 本地网页控制台 |
| `start_web.py` | 自动启动本地网页并打开浏览器 |
| `open_web.ps1` | Windows PowerShell 网页启动脚本 |
| `query_seats.py` | 查询座位显示号和实际 `seat_id` |
| `test_book_cancel.py` | 测试预约后立即取消 |
| `config.example.yaml` | 配置模板 |
| `booking.example.json` | 候选座位模板 |
| `requirements.txt` | Python 依赖 |

本地生成但不要上传：

- `config.yaml`
- `booking.json`
- `cookies.json`
- `*.log`
- `last_booking*.json`

## 常见问题

### 页面打不开

确认网页服务是否启动：

```powershell
python start_web.py
```

然后打开 `http://127.0.0.1:8765`。

### 登录失败

重新手动登录：

```powershell
python book.py --show-browser --dry-run
```

### 验证座位配置失败

检查：

- `room_name` 是否和网页里显示的房间名一致
- `seat_num` 是否是平面图上的显示号
- 是否已经读取过对应房间的座位和平面图

### 到 20:00 没抢

检查：

- 电脑是否开机
- Windows 任务计划是否已安装
- `settings.trigger_time` 是否为 `20:00`
- cookie 是否过期
- 网络是否正常

## 安全提醒

- 不要把真实账号密码提交到 GitHub
- 不要上传 `cookies.json`
- 建议 GitHub 仓库使用 private
- 如果误上传过账号密码，请立即修改密码并清理仓库历史
