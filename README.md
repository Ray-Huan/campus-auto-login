# campus-auto-login

校园网自动登录脚本。常驻后台轮询网络状态，检测到「未联网且认证门户可达」时，自动通过本机 Edge 完成认证登录，登录成功后关闭系统弹出的认证页面。

## 特性

- 自动检测：网络切换后数秒内完成登录，无需手动操作
- 无打扰：默认无头登录，不弹窗口；识别「已登录」状态，不会反复登录
- 可配置：账号、密码、运营商、门户地址均通过 `config.ini` 配置
- 登录完成后自动关闭 Windows 弹出的「上网登录页」窗口

## 依赖

- Windows
- Python 3.9+
- 本机已安装 Microsoft Edge
- `playwright`（Python 包，无需额外下载浏览器）

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

- `isp` 填写门户下拉框中运营商的文字（如 `中国移动` / `中国电信` / `中国联通`）
- `url` 为认证门户地址，不同地区/学校可能不同，按实际情况修改

## 运行

```bash
python campus_login.py          # 常驻后台
python campus_login.py --once   # 单次尝试后退出（调试用）
```

日志输出到 `logs/campus_login.log`。

## 开机自启

在启动文件夹放置一个 VBS 脚本，登录 Windows 后静默运行：

`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\campus_login_auto.vbs`

```vbs
CreateObject("WScript.Shell").Run """<pythonw.exe 路径>"" ""<campus_login.py 路径>""", 0, False
```

取消自启：删除上述 `.vbs` 文件即可。

## 说明

`config.ini` 与 `logs/` 已加入 `.gitignore`，不会被提交到仓库。
