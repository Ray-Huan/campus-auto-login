# 校园网自动登录

校园网认证自动登录脚本。后台常驻轮询网络状态，发现未联网且认证门户可达时，自动用本机 Edge 完成登录，登录成功后关闭系统弹出的认证页面。

登录默认无头模式，不弹窗口、不抢焦点；门户已处于登录状态时自动跳过，不会重复登录。

## 依赖

- Windows
- Python 3.9+
- Microsoft Edge（使用系统已装的 Edge，无需额外下载浏览器）
- Python 包：`playwright`

```bash
pip install playwright
```

## 配置

```bash
cp config.example.ini config.ini
```

编辑 `config.ini`：

```ini
[account]
username = 你的账号
password = 你的密码
isp = 中国移动

[portal]
url = http://192.168.0.101/
```

- `isp`：门户下拉框里运营商的文字，如"中国移动"、"中国电信"、"中国联通"
- `url`：认证门户地址。不同地区、不同学校的门户地址不一样，按实际情况修改

## 运行

```bash
python campus_login.py          # 常驻后台
python campus_login.py --once   # 单次尝试后退出（调试用）
```

日志在 `logs/campus_login.log`。

## 开机自启

在启动文件夹放一个 VBS 脚本，登录 Windows 后即静默运行。
文件位置：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\campus_login_auto.vbs`

```vbs
CreateObject("WScript.Shell").Run """<pythonw.exe 路径>"" ""<campus_login.py 路径>""", 0, False
```

取消自启：删除这个 .vbs 文件即可。

## 说明

`config.ini` 和 `logs/` 已在 `.gitignore` 中，不会被提交到仓库。
