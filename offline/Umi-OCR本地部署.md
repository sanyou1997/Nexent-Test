# Umi-OCR 本地部署

适用于本次定制的 Nexent 2.8.5，以及支持 AVX 的 x86-64 主机和 Linux 容器。镜像内已包含 Umi-OCR 2.1.5、PaddleOCR-json 引擎和识别模型。此目录提供 Umi-OCR 服务镜像。Nexent 平台使用本次更新后的后端、前端及生产 Compose 文件。

## 在客户主机导入服务

1. 将 `umi-ocr-2.1.5-linux-amd64.tar` 和相邻的校验文件复制到客户主机。Linux 可运行 `sha256sum -c umi-ocr-2.1.5-linux-amd64.tar.sha256`，PowerShell 可使用 `Get-FileHash` 比对。
2. 在镜像文件所在目录运行 `docker load -i umi-ocr-2.1.5-linux-amd64.tar`。
3. 在本次定制的 Nexent 项目根目录启动服务。如平台采用其他 Compose 项目名或环境文件，请沿用现场配置。

```sh
docker compose -p nexent --env-file deploy/docker/.env.generated --env-file deploy/env/.env -f deploy/docker/compose/docker-compose.prod.yml --profile local-umi up -d --no-deps --pull never nexent-umi-ocr
```

首次部署时，按更新后的 Compose 配置启动 `nexent-config`、`nexent-runtime`。这两个服务与 Umi-OCR 通过内部网络 `nexent_local_ocr` 通信，已有实例升级时需要接入该网络。Umi-OCR 无需发布宿主机端口。若现场地址段冲突，先在部署环境设置 `LOCAL_OCR_SUBNET` 为可用的 Docker 子网。

## 租户管理员验收

打开“模型管理 → 文件解析 → PDF 文档”，选择 Umi-OCR。服务根地址填写 `http://nexent-umi-ocr:1224`。点击“试解析”，核对两页样本和第二页的 6,000.00、8,000.00 和 14,000.00。金额可带或不带千分位。保存后，对本租户启用 PDF 解析的智能体生效。空白页保留页码，关键文字和表格关系请对照原件。

## 接入已有开源 OCR

- MineU 使用本系统现有兼容的 `/tasks` 提交、查询及结果接口。
- Umi-OCR 使用 `POST /api/ocr`。平台将 PDF 逐页转成 PNG，以 Base64 提交，按物理页顺序整理识别文字。
- PaddleOCR 3.x 通用 OCR 使用本地 PaddleX 服务 `POST /ocr`，PP-StructureV3 使用 `POST /layout-parsing`。平台按页发送 PDF。前者读取识别文本，后者读取每页的 Markdown，可保留服务返回的表格。客户应在内网预置运行依赖、模型文件，并启动所选流水线的 HTTP 服务。
- 客户自建服务如使用不同的上传格式、路径或返回字段，需要在本地转换为上述一种协议，或为平台增加对应适配。界面填写后端可达的服务根地址。新增 PaddleOCR 或 Umi-OCR 地址由部署方加入 `LOCAL_OCR_ALLOWED_ORIGINS`，配置和运行服务使用同一份设置。

本地文件解析与对话大模型分别配置。当前审理智能体的对话模型使用外部接口，整套系统迁移至隔离网时还需配置本地对话模型及依赖的其他模型服务。
