# GOFO 站点工具包（本地运行）

每台机器本地跑一个小服务 + 一个全屏浏览器大屏。两把 USB-COM 扫码枪并行接入，统一进串行队列：

- **扫到 `QR_` 开头** → 司机签退。屏幕显示 **应取 / 实取 / 错扫** 三数。
  - 强提醒（红底 + 语音“取件量低”）：`应取−实取 > X` 或 有错扫 或 接口判定异常。
  - 弱提醒（黄底）：`应取−实取 ≥ 弱阈值`。
  - 数对不上 / 有错扫 → **队列暂停**，员工在大屏点「放行 / 拒绝」后才继续。
- **其余条码** → 自动查单并用 CLodop 打印 4×6" 面单。

签退需人工放行时，后面排队的面单会等待，放行/拒绝后再打印（串行队列天然保证顺序）。

## 运行

1. 装 Python 3.10+（勾选 Add to PATH）。
2. 装 **CLodop Web 打印服务**（本目录上级的 `CLodop_Setup_for_Win32NT.exe`）——打印面单必须。
3. 双击 **`run.bat`**：自动装依赖、起服务、全屏打开大屏。
4. 打开后台配置：双击 `admin.bat` 或浏览器访问 `http://127.0.0.1:5000/admin`（默认密码 `admin123`，请尽快修改）。

## 后台配置项（/admin）

- **DMS 账号**：站点账号密码（PDA 签退 + 网页打印通用）。可「测试登录」。
- **打印**：DMS 网页版 token（`Bearer …`）。可用测试单号「测试查单」。
  > 生产建议接入网页登录自动刷新 token（见下）。测试期手动贴一个即可。
- **提醒设置**：强阈值 X（默认 5）、弱阈值（默认 1）、错扫是否算强、强提醒是否播声音、面单尺寸、打印机名。
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

网页版 `https://dms.gofoexpress.com/prod-api`（需 `Authorization: Bearer <JWT>`）：

| 用途 | 方法 | 路径 |
|---|---|---|
| 换单打印查单 | POST | `/ops/scan/labelReplace/getLabelInfo`（`{scanNumber}`） |

**待后续更新的接口**（已反编译到，供扩展）：
- 扫描查单：`gfs-site/appWaybillScanQuery/scanQuery/v2`
- 退回转运中心：`gfs-site/centerReturn/scanCheck`、`centerReturn/submit`、`centerReturn/batch/submit`、`centerReturn/refuse`

## 待接入：网页版自动登录（免手动贴 token）

`dms_web_client.py` 里 `WEB_LOGIN_PATH` 待抓包填入：在 DMS 网页登录时 F12→Network 抓那条返回
token 的登录 POST（URL / 请求体 / 响应），填好后打印 token 可用账号密码自动获取并过期自动刷新。

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
