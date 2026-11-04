# ckb-release-monitor

每天检查 [nervosnetwork/ckb](https://github.com/nervosnetwork/ckb/releases) 发布的二进制有没有被篡改（比如管理员账户被盗后有人投毒）。
Daily integrity check of nervosnetwork/ckb release binaries against tampering (e.g. a compromised admin account poisoning releases).

## 检查内容 / What is checked
最近 `MONITOR_DAYS`（默认 183 天）内发布的非草稿 release，包括预发布版本。超出窗口的旧 release 不再检查，但它们的基线文件会留在仓库里。
Non-draft releases (incl. prereleases) published in the last `MONITOR_DAYS` (default 183). Baselines for older releases stay but are no longer checked.

每个 release 记录在 `baseline/<tag>.txt` / Per release recorded in `baseline/<tag>.txt`: tag → commit SHA (annotated tags dereferenced), published_at, author, prerelease flag, and per-asset name / size / **sha256**. If the release ships a checksum file, computed hashes are also verified against it.

- 没有基线（新版本）→ 自动开一个带 `new-baseline` 标签的 PR，需人工审核，不会自动合并。/ No baseline → PR labeled `new-baseline` (no auto-merge).
- 有任何差异（哈希变化、附件增删、tag 被移动、checksum 不匹配）→ job 失败，发送 Discord 告警（如果设置了 webhook），并在本仓库开或更新以 tag 为标题的 issue。/ Any diff → job fails, Discord alert (if configured), issue opened/updated per tag.

## 本地运行 / Run locally (one command)
```
./scripts/check.sh            # needs gh (authenticated) + jq
MONITOR_DAYS=365 ./scripts/check.sh
```
退出码 0 表示没有差异，1 表示有告警（见 `out/alert.txt`）。/ Exit 0 = clean, 1 = alert.

## 威胁模型 / Threat model
- 基线存在 release 之外的地方（本仓库），所以攻击者同时替换二进制和 checksum 文件也能被发现。/ Baseline lives outside the release, so swapping binaries + checksum files together is still detected.
- **本仓库的管理员应该和 ckb 仓库的管理员是不同的人**，否则同一批账户被盗时，监控也会一起失效。/ **Admins of this repo should differ from ckb admins**, otherwise one compromise defeats both.
- 只能发现发布*之后*的篡改。如果发布时就已被投毒，需要靠审核新基线 PR 时的交叉核对来发现。/ Only detects post-publication tampering; poisoning at release time must be caught when reviewing the new-baseline PR.

## 设置 Discord 告警 / Discord webhook
Settings → Secrets and variables → Actions → New repository secret: `DISCORD_WEBHOOK` = webhook URL. 不设置也能跑，只是不发 Discord 告警（issue 照常创建）。/ Optional; skipped if unset.
```
gh secret set DISCORD_WEBHOOK -R gpBlockchain/ckb-release-monitor
```

## 审核新基线 PR / Reviewing new-baseline PRs
1. 核对 commit SHA 是否就是 ckb 主分支上预期的发版 commit。/ Commit SHA matches expected release commit.
2. 从源码构建（同一 commit、同一工具链），比对哈希；或者核对官方签名 / 官方在其他渠道公布的哈希。/ Build from source and compare, or verify official signatures / hashes published through other channels.
3. 检查作者和附件列表是否正常。/ Author and asset list look normal.
4. 确认无误后再合并。/ Merge only then.

运行时间：每次都会下载窗口内所有附件（每个 release 几百 MB），通常需要几分钟。/ Runtime: downloads all assets in window each run, typically a few minutes.
