# 远程工作台部署记录

网页：`https://www.lanxi1023.com/studio/`。
拟用接口：`https://api.lanxi1023.com`，HTTPS 通道目标是 `http://127.0.0.1:8769`。

## 当前边界

GitHub Pages 仅发布 `release/studio/` 中明确列出的网页文件。`data/` 中的账号、登录密码、会话、作品、上传图片及数据库不发布。
默认构建显示“远程连接待开通”，不会向尚未确认归属的接口发送登录密码。

本机 `8768` 是现有工作台，`8000` 是号池，`9000` 是浏览器管理接口，这些端口不能作为外网通道的目标。只允许通过 `8769` 的创作接口白名单访问。

## 开通步骤

1. 运行 `setup_remote.py`，一次性生成本地登录凭据；用户名、密码保存在 `data/remote-login.txt`，服务端只使用 `remote-auth.json` 中的 scrypt 哈希。
2. 确认采用的远程通道服务及域名接入。域名当前由 DNSPod 管理，Cloudflare 固定域名方案需先处理 DNS 托管条件；不能只添加一个 CNAME 就假设免费固定通道可用，也不能未经核对迁移整个域名的解析。
3. 完成 HTTPS、固定主机名、原网站和邮箱解析核对后，在 `data/remote-config.json` 配置真实 API 域名与精确网页 Origin，并设 `enabled=true`。
4. 本机工作台运行后，用 `start-remote.ps1` 启动仅监听回环地址的远程网关。将通道接到 `127.0.0.1:8769`，不改变本机工作台的访问限制。
5. 外部验证匿名作品访问失败、登录、参考图上传、纯文字/参考图生成、轮询、预览、单图及 ZIP 下载、退出和会话过期。完成手机移动网络测试后，才以 `build_remote.py --ready` 构建正式入口并发布。
6. 设置通道和本地服务自动恢复；电脑需保持开机、联网、不休眠。网页关闭不影响已提交到本机的任务。验证码仍在号池电脑处理。

前端与接口使用同一个根域名下的 HTTPS 地址，以便手机浏览器正常携带登录 Cookie。跨根域部署需重新设计登录方式，不能简单放宽 Cookie 和 CORS。

## 验证

`test_remote_gateway.py` 验证登录、Secure/HttpOnly Cookie、CSRF、CORS、接口隔离、注销和失败登录限流。
`test_remote_ui.py` 使用临时凭据和模拟 HTTPS 域名，连接现有本地作品，验证桌面/手机布局、登录、素材与图片读取、选择打包下载、退出。它不会提交生图任务或更改实际账号。

本机模拟通过并不代表外网通道已部署。真实域名、网络速度、手机网络及生图端到端测试需在开通后完成。

## 2026-11-02 固定连接接入

已在用户 Cloudflare 账号创建 `inspiration-studio` 固定通道，API 域名为 `api.lanxi1023.com`，只连接回环端口 `8769`。
DNSPod 中全部三条网站记录已核对保留为 DNS only；DNS 托管已切换至 Cloudflare，域名注册仍在腾讯云。公网 HTTPS 健康检查已通过。

连接令牌仅放在本机 `data/tunnel-token.txt`，使用当前 Windows 用户及 SYSTEM 可访问的文件权限，不进入 GitHub。`run-background.ps1` 配合 `InspirationStudioBackground` 登录启动任务，每 30 秒检查本机服务和通道，发现退出后恢复。

完整外网生图验收记录保存在本机 `data/remote-live-check.json`，其中 `state=completed` 才表示真实生图、图片读取和 ZIP 下载均已通过。真实移动网络的可达性仍取决于用户手机网络。

## 多账号隔离

工作台登录账号与号池的 ChatGPT 账号独立。登录账号共用调度资源，服务端按已验证会话确定数据归属，不接受客户端指定归属。历史任务、收藏和上传素材迁移到 `01`，新增账号为 `02`、`03`、`04`，凭据仅存本机 `data/remote-accounts.txt`。

任务、批次、图片、收藏、重试、继续创作、打包下载、上传文件和私人素材均按账号授权。内置画风、衣服和姿势素材共用。浏览器草稿按账号分开保存，跨标签页换号会重新载入页面。

远程网关通过本机 `bridge-secret.txt` 对账号上下文签名，核心服务验证后再执行操作。不能把任意客户端传来的账号头当成已登录身份。仅本机回环访问保留管理员和号池读取参考文件的能力；电脑管理员始终能够通过本机文件管理数据。

新版登录要求 `X-Studio-Client: accounts-v1`，旧网页会提示刷新；迁移时旧会话全部失效，避免旧版共用草稿逻辑串号。上线前执行 `test_account_isolation.py`，外网验证使用 `test_accounts_live.py`，两者不会使用用户上传的私人图片作为测试素材。


### 无命令窗口启动（2026-11-03）

开机登录任务改为 `pythonw.exe background.py run`。桌面入口也使用 pythonw；后台服务使用 `CREATE_NO_WINDOW` 启动并直接写日志，不再通过隐藏的 PowerShell 窗口启动。后台每 30 秒检查本机服务和固定通道，单实例锁避免重复启动。`start.ps1`、`run-background.ps1` 与 `start-remote.ps1` 保留为兼容入口。日志保存在本机 `data/background.log` 和各服务日志中。

### 个人快捷链接（2026-11-03）

`create_quick_link.py` 只在本机为指定的账号创建可重复使用的快捷入口，默认归属内部账号 `01`。链接是等同登录凭证的随机 256 位令牌，放在 URL fragment 中；前端读取后立即清除地址栏的 fragment，再通过 HTTPS POST 兑换普通的 HttpOnly 会话，不把密码或通行令牌放在查询参数、素材请求、localStorage 或公开文件中。服务端 `shortcuts` 表只保存令牌摘要和归属账号；原有账号隔离、CSRF 和接口白名单保持有效。链接无固定到期时间，可由本机工具 `--revoke` 停用，或 `--rotate` 更换；停用同时使由该链接签发的会话失效。停用工作台账号也会阻止快捷登录。

原始链接仅写入本机 `data/quick-link-01.txt` 和 `E:\号池\工作台快捷链接.txt`，文件仅限当前 Windows 用户和 SYSTEM 访问。不得发布到 GitHub、网页源码或日志中。
