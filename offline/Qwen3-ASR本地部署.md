# Qwen3-ASR 本地部署

本包使用 Qwen3-ASR-0.6B 的 sherpa-onnx INT8 转换版，在 x86-64 CPU 上推理。模型、FSMN-VAD、解码器和运行依赖均在镜像内。与本次定制的 Nexent 配套使用。

## 内网启动

将本目录中的 Qwen 镜像和相邻的 SHA-256 校验文件复制到客户机。核对校验值后导入镜像，在本次定制的 Nexent 项目根目录启动服务。

```sh
docker load -i qwen3-asr-0.6b-int8-linux-amd64.tar
docker compose -p nexent --env-file deploy/docker/.env.generated --env-file deploy/env/.env -f deploy/docker/compose/docker-compose.prod.yml --profile local-qwen-asr up -d --no-deps --pull never nexent-qwen-asr
```

配置服务与运行服务须接入同一内部网络 `nexent_local_ocr`。镜像默认无宿主机端口映射。部署至其他平台时按现场配置调整项目名与环境文件。

## 租户设置

打开“模型管理 → 文件解析 → 音频转写”，选择 Qwen3-ASR。服务地址为 `http://nexent-qwen-asr:8000`，模型名为 `Qwen/Qwen3-ASR-0.6B`。点“试转写”核对样本，保存后对本租户启用音频解析的智能体生效。原有租户默认使用 Paraformer。

本机服务采用 FSMN-VAD 定位语音段，按段调用 Qwen3-ASR。返回的起止时间用于回听。服务一次处理一份录音，同时到达的请求可能提示忙，请在上一份完成后重试。服务端单份上限为 2 小时、256 MiB，聊天附件同时受平台上传上限约束。

## 接入客户已有的 Qwen3-ASR

客户自建的服务应支持 `POST /v1/audio/transcriptions`，接收多部分表单字段 `file`、`model` 和 `response_format=json`。部署方将其内网根地址加入 `QWEN_ASR_ALLOWED_ORIGINS` 并使配置与运行服务重新载入。在租户页面填写根地址和服务实际公布的模型名。

平台支持只有 `text` 的 JSON 响应，附件详情显示正文，聊天引用文件名。服务同时返回 `duration` 和 `segments` 时，每段使用以秒为单位的 `start`、`end` 及 `text`。案件材料中的姓名、金额、数字、讲话人应核对原音。

原模型来自 [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)，本镜像使用 [sherpa-onnx 发布的转换权重](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html)。
