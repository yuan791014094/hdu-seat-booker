# HDU Library Seat Booker

杭电图书馆自习室抢座工具，主要面向自习室座位预约，默认围绕 `宋韵云图（四楼）`。

## 本地使用

第一次使用先复制模板：

```powershell
copy config.example.yaml config.yaml
copy booking.example.json booking.json
```

然后在 `config.yaml` 填杭电账号密码，在 `booking.json` 填候选座位。

启动网页：

```powershell
python web_app.py
```

打开：

`http://127.0.0.1:8765`

## GitHub Actions

不要提交真实的 `config.yaml`、`booking.json`、`cookies.json`。

如需用 GitHub Actions 自动运行，在仓库 Settings -> Secrets and variables -> Actions 添加：

- `HDU_USERNAME`：杭电账号
- `HDU_PASSWORD`：杭电密码
- `BOOKING_JSON`：完整的 `booking.json` 内容

Actions 默认北京时间 20:00 运行。
