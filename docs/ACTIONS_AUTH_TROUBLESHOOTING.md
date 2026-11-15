# Actions 无法启动与学校二次认证

## GitHub 返回仓库已禁用

当触发工作流返回 `HTTP 422: Actions has been disabled for this repository`，
先检查仓库设置与目标工作流状态：

```bash
gh api repos/xiaoberber8-ai/Fudan_iCourse_Subscriber/actions/permissions
gh api repos/xiaoberber8-ai/Fudan_iCourse_Subscriber/actions/workflows/381745615
```

2026-11-15 实测仓库 `enabled=true`、目标工作流 `active`，手动触发仍为上述 422。
仅重新启用目标工作流后结果也相同，说明当前被 GitHub 的独立平台限制阻止启动。
工作流代码无法解除这个限制；具体禁用原因需 GitHub Support 确认。

[GitHub 官方说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)
指出，GitHub 控制的禁用状态需要联系支持审核。
公开仓库使用标准 GitHub 托管 runner 的运行分钟数免费，
不能仅凭页面的通用提示断定免费分钟耗尽。
[计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

请仓库所有者登录 [GitHub Support](https://support.github.com/contact?tags=dotcom-actions)，
选择仓库/Actions 限制相关问题，提交下列内容：

> Subject: Review Actions restriction for xiaoberber8-ai/Fudan_iCourse_Subscriber
>
> I own https://github.com/xiaoberber8-ai/Fudan_iCourse_Subscriber.
> The Actions UI reports that Actions is disabled for this repository.
> The repository permissions API returns enabled=true, and workflow 381745615
> is active. Re-enabling this workflow succeeds, but dispatching it on main
> still returns HTTP 422: "Actions has been disabled for this repository."
> Run 43094513655 remains queued without jobs. The workflow uses ubuntu-latest.
> Please review the restriction, explain its specific cause, and restore access
> if eligible. Please tell me any required workflow or account changes.

不要反复重启工作流或通过切换仓库权限尝试解除平台限制。

## CAS 返回 code=200，但没有 loginToken

2026-11-15 的仅登录探测在 WebVPN 登录成功后，观察到 iCourse CAS 返回如下结构：

```json
{"code": 200, "second": true, "loginToken": null}
```

这表示密码认证后还需要额外认证，尚未取得完成登录的令牌。
程序会抛出 `AdditionalAuthenticationRequired`，明确提示人工处理并立即停止重试。
错误中不会打印完整认证响应、个人信息或令牌。

请在浏览器通过学校 WebVPN 打开 iCourse，查看并完成学校要求的验证步骤。
本机浏览器完成验证不保证后续全新 GitHub runner 会话能够直接登录；
若云端仍要求额外认证，需要咨询学校支持，确认受支持的无人值守访问方式。
不要将验证码、会话 Cookie 或完整登录响应写入仓库、Issue 或 Actions 日志。

在已配置 `StuId` 和 `UISPsw` 环境变量的环境中，可仅检查登录：

```bash
python -c 'from src.api.webvpn import login_with_retry; login_with_retry(max_attempts=1)'
```

该命令仅建立登录会话，不处理课程或写入数据库。只有 iCourse 用户信息 API
也验证成功，才表示登录已恢复。超时等暂时故障仍允许完整重试；
明确要求额外认证的响应不会再次提交密码。

## WebVPN 报成功后立刻 session cold

HTTP 200 可能只是一个未认证的页面。程序现在同时检查 WebVPN 会话 Cookie
与不跟随重定向的门户探测，确认认证有效后才报告成功。
票据交换超时后会验证已获得的会话；若没有有效会话，由外层重新登录取得新票据。
不会重放可能已经被消费的旧票据。
[CAS 协议规定服务票据仅能用于一次验证尝试](https://apereo.github.io/cas/development/protocol/CAS-Protocol-Specification.html)。

GitHub 允许任务启动和学校允许完成认证是两个独立条件；恢复订阅需要同时满足。
