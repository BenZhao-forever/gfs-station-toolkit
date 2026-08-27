# GOFO 站点工具包（本地运行）

每台机器本地跑一个小服务 + 一个全屏浏览器大屏。两把 USB-COM 扫码枪并行接入，统一进串行队列：

- **扫到 `QR_` 开头** → 司机签退。屏幕显示 **应取 / 实取 / 错扫** 三数。
  - 强提醒（红底 + 语音“取件量低”）：`应取−实取 > X` 或 有错扫 或 接口判定异常。
  - 弱提醒（黄底）：`应取−实取 ≥ 弱阈值`。
  - 数对不上 / 有错扫 → **队列暂停**，员工在大屏点「放行 / 拒绝」后才继续。
- **其余条码** → 自动查单并用 CLodop 打印 4×6" 面单。

签退需人工放行时，后面排队的面单会等待，放行/拒绝后再打印（串行队列天然保证顺序）。

## 运行

1. 装 Python（勾选 **Add python.exe to PATH**）。
   - Windows 10/11：Python 3.10+ 均可。
   - **Windows 7：必须用 Python 3.8.10**（3.10+ 在 Win7 装不上）。
2. 装 **CLodop Web 打印服务**（本目录上级的 `CLodop_Setup_for_Win32NT.exe`）——打印面单必须。
3. 双击 **`run.bat`**：自动装依赖、起服务、全屏打开大屏。
4. 打开后台配置：双击 `admin.bat` 或浏览器访问 `http://127.0.0.1:5000/admin`（默认密码 `admin123`，请尽快修改）。

## Windows 7 说明

可以在 Win7 上运行，注意：

- **Python 用 3.8.10**（python.org 上 Win7 能装的最后版本；3.10+ 装不上）。核心依赖已锁定
  Flask 3.0 / Werkzeug 3.0，兼容 3.8。
- **浏览器**：Win7 上 Chrome / Edge 最高到 **v109**，大屏用的都是标准 JS/CSS，v109 正常。
  若没装 Chrome，`run.bat` 会自动回退用 Edge。
- **密码加密（cryptography）为可选**：Win7 上可能装不上，`run.bat` 会静默跳过，程序照常运行
  （本地单机，DMS 密码只存本机 `data/` 文件，不上云、不进仓库）。想启用加密可自行
  `pip install cryptography`（能装上就自动启用）。
- **CLodop** 支持 Win7。若弹 “Load SSL Error” 是它自带的 https(8443) 证书问题，与打印无关，
  点「确定」即可——本工具走 http(8000) 打印。

## 后台配置项（/admin）

- **DMS 账号**：站点账号密码（PDA 签退 + 网页打印通用）。可「测试登录」。
- **打印**：拿 DMS 网页版打印 token。三种方式：
  1. **自动登录（推荐）**：点「自动登录获取 token」，本地 `ddddocr` 识别验证码、账号密码自动登录，
     过期后台自动重登——无人值守。
  2. **手动输验证码登录**：ddddocr 没装时，点「获取验证码」看图输入后登录。
  3. **手动贴 token**：从浏览器 F12 复制 `Bearer …` 应急。
  可用测试单号「测试查单」验证。
- **提醒设置**：强阈值 X（默认 5）、弱阈值（默认 1）、错扫是否算强、强提醒是否播声音、
  **自动放行阈值**（应领−实领 < 此值且无错扫、实领>0 时自动放行，默认 3）、面单尺寸、打印机名。
- **语音播报**：签退/放行成功 → “签退成功”；实领 0 → “请先收件”；强提醒 → “取件量低”；任何异常 → “签退异常”。
  拒绝放行不需填理由，直接点「拒绝」。
- **扫码枪**：勾选两个 COM 口 + 波特率（默认 9600），保存后重启程序生效。
- **更新**：填公开仓库 `owner/repo`，可「检查更新 / 立即更新并重启」。
- **日志**：签退异常、放行/拒绝、打印失败等流水（最近 500 条）。
- **安全**：改管理员密码。

## 接口地图（反编译自 GOFO PDA + DMS 抓包）

PDA 端 `https://dms-public-api.gofoexpress.com`（复用签到项目鉴权，请求头 `type:4` 等）：

| 用途 | 方法 | 路径 |
|---|---|---|
| 登录 | POST | `/app/auth/sitePda/login` |
| 扫码签退 | GET | `/apple/deliver/signOut/scanSignOut?uuid=` |
| 签退放行 | POST | `/apple/deliver/signOut/pass`（`{signRecordId, driverUserId}`） |
| 签退拦截 | POST | `/apple/deliver/signOut/intercept`（`{signRecordId, driverUserId, interceptRemark}`） |

签退返回体 `StaSignScanResp` 关键字段：`beReceiveCount`(应取) / `receivedCount`(实取) /
`wrongScanWaybillCount`(错扫) / `returnCount` / `signResult`(1成功) / `signRecordId` / `driverUserId`。

网页版 `https://dms.gofoexpress.com/prod-api`（需 `Authorization: Bearer <JWT>`，登录带验证码见下）：

| 用途 | 方法 | 路径 |
|---|---|---|
| 单号 → 内部 waybillId | POST | `/waybill/list`（`{waybillNo}` → `rows[0].id`） |
| **官方面单 PDF（打印用）** | POST | `/waybill/batchPrint`（`{waybillIds:[id],waybillType:1}` → `data[0].url` = S3 PDF） |
| 面单字段（自排版兜底） | POST | `/ops/scan/labelReplace/getLabelInfo`（`{scanNumber}`） |

**打印首选官方 PDF**：`/waybill/list` 拿内部 id → `/waybill/batchPrint` 拿 DMS 官方渲染的 4×6" 面单
PDF（S3 预签名链接，含所有版式），交 CLodop `ADD_PRINT_PDF` 打印。取不到时自动兜底用
`getLabelInfo` 的字段本地排版打印。

DMS 网页登录（RuoYi 框架，带图形验证码）：`GET /captchaImage` 取图 → `ddddocr` 本地识别 →
密码 **AES-CBC 加密**（key=iv=`59SO+p2dXTeghIqm`）→ `POST /login {username,password,code,uuid}`
→ `GET /getInfo`（必调，否则接口"200 但空数据"）。装了 ddddocr 即全自动、过期自动重登；
装不上（如 Win7 的 onnxruntime）则后台手动输一次验证码。

**待后续更新的接口**（已反编译到，供扩展）：
- 扫描查单：`gfs-site/appWaybillScanQuery/scanQuery/v2`
- 退回转运中心：`gfs-site/centerReturn/scanCheck`、`centerReturn/submit`、`centerReturn/batch/submit`、`centerReturn/refuse`

## 网页版自动登录（已实现）

DMS 网页版是 RuoYi 框架，登录 `POST /prod-api/login`，要点（见 `../DMS接入要点.md`）：
- 密码需 **AES-CBC 加密**（固定 key/iv `59SO+p2dXTeghIqm`）后再提交；
- 登录带**图形验证码**，用 **ddddocr** 本地识别（4 位数字/字母，识别错就换一张重试）；
- 登录成功后**必须调一次 `/getInfo`**，否则很多接口"200 成功但空数据"。

程序已封装：`dms_web_client.login_auto()` 全自动完成上述流程；`keepalive()` 每 10 分钟用
`getInfo` 续期，token 失效自动重登。ddddocr 装不上（如 Win7 的 onnxruntime）时退回后台人工输码。

## 目录

```
app.py            本地服务 + 串行队列（签退阻塞、打印排队）
gfs_client.py     PDA 端：登录 / 签退 / 放行 / 拦截
dms_web_client.py 网页端：换单打印查单（Bearer token）
store.py          本地配置存储（单管理员，密码 Fernet 加密）
updater.py        GitHub 公开仓库自动更新（version.json + zip）
serial_reader.py  两把 USB-COM 扫码枪读取入队
templates/        kiosk.html 大屏 · admin.html 后台 · label.html 4x6 面单
static/           前端 + JsBarcode/QR + CLodop 桥 + 语音
version.json      当前版本号（自动更新比对用）
run.bat           一键启动（装依赖 + 起服务 + 全屏大屏 + 更新重启循环）
```
