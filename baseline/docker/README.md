# Initial Docker baseline snapshots

采集日期：2026-11-23（Asia/Shanghai）。来源：Docker Hub Registry V2 的
`nervos/ckb` 版本标签、`x64-<tag>` 和 `aarch64-<tag>`。

本目录随 Docker 监控功能 PR 一并提交首次采集的 5 份 JSON：
`v0.207.0-rc1`、`v0.207.0`、`v0.208.0`、`v0.209.0`、`v0.210.0`。
采集时已验证 manifest 原始字节的 SHA256、子 manifest digest，
并核对两种架构标签与版本索引一致；`latest` 当时与 `v0.210.0` 一致。

这些数据是首次观测快照，不是独立验证过的发布证明；尚未通过签名、可复现构建
或其他独立可信渠道核对。请在合并 PR 前审核：合并后脚本会将这些 JSON 作为
已接受的比较基线，后续变更只告警、不自动覆盖。

各 JSON 包含版本索引 digest，以及每个平台的 image digest、config 和 layer
描述符（digest、size、mediaType）；不包含镜像 layer 实际字节。
