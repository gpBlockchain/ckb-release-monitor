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

## Docker 镜像完整性审核 / Docker image integrity review

每日同一 CI 还执行 `python3 scripts/check-docker.py`，通过 Docker Hub Registry
V2 API 读取 `nervos/ckb`，不启动容器，也不需要本地安装 Docker。
Release 检查失败时，Docker 检查仍会运行；两者共享基线 PR、Issue 和 Discord 告警。

- 对窗口内 Release 的 `vX.Y.Z[-rcN]`、`x64-vX.Y.Z[-rcN]`、
  `aarch64-vX.Y.Z[-rcN]` 记录 manifest/index 的原始 SHA256，以及各平台的
  image/config/layer descriptors（digest、size、mediaType）。核对两个架构标签与版本索引一致。
- 实际计算下载到的 manifest 字节哈希，验证响应 digest 和父索引指定的子 manifest digest，
  而非仅信任 registry 响应头。标签重指向、平台增删、描述符变化都会告警；
  404、限流重试耗尽、超时、解析失败也令检查失败，区别于已确认的内容差异。
- `baseline/docker/<tag>.json` 是人工审核基线；首次发现生成候选 PR，绝不自动合并。
  已有基线出现差异只写 `out/docker-<tag>.diff`，不覆盖基线。
  Docker 基线一旦审核入库，即使 Release 被删除或超过 183 天也持续检查。
- `latest` 是浮动标签：它的各平台镜像必须与 GitHub 上发布时间最新的正式 Release
  一致（排除 prerelease 和 rc 标签）。正常发新版不因 digest 改变而误报；
  回退到旧版、指向其他镜像、平台元数据变化会告警。发布过程尚未完成时可能暂时告警，
  待镜像发布完成后重新运行；不静默忽略缺失镜像。
- 可用 `DOCKER_REPOSITORY=namespace/repository` 覆盖 Docker Hub 镜像仓库。
  `UPSTREAM`、`MONITOR_DAYS` 与 Release 校验相同。Docker 拉取令牌仅为匿名只读令牌，
  不向 Docker Hub 传递 `GH_TOKEN`。

**审核新 Docker 基线时**：从独立可信渠道或已验证的构建产物核对两个平台的 image digest；
检查 config/layer descriptors 和版本索引。仅凭同一次 registry 查询结果，不足以证明首次发布可信。

**边界**：这是发布后完整性监控，不是镜像漏洞/CVE 扫描，不验证源码到镜像的可复现构建或签名；
不会下载/解包全部 layer 或逐字节比对镜像内 CKB 与 Release 的 CKB。记录的 layer digest
能发现 tag/manifest 层面的替换，但本检查不验证 registry 是否仍保存所有 blob，或 blob 实际字节是否损坏。

依据：[CKB 官方 Docker 发布配置](https://github.com/nervosnetwork/ckb/blob/develop/.github/workflows/build_ckb_image.yml)、
[Registry V2 规范](https://distribution.github.io/distribution/spec/api/)。

### 回归测试 / Regression tests

```sh
python3 -m unittest discover -s tests -v
bash -n scripts/check.sh
```

测试不访问网络，覆盖新基线、无变化、镜像替换、标签删除、架构缺失/不一致、
`latest` 正常升级/异常改指向、平台元数据变化、请求失败、旧基线持续监控、
manifest 字节 digest 验证和已有 Release 告警保留。
