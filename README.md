# Nexent 本地部署与纪委案件审理辅助

在 Nexent v2.8.5 基础上提供按租户配置的本地文件解析、审理意见四段式输出、法规原件预览和引用标黄。项目以覆盖文件配合固定的上游版本发布。首次恢复需要 Git、Python 3.10+、Docker Compose v2，以及拉取 GitHub、Docker 镜像和构建依赖的网络。Windows 使用 Docker Desktop 的 Linux 容器和 Git for Windows，并预留 Nexent 服务端口。

## 新电脑首次安装

新电脑只需取得本仓库。审理智能体模板、9 份法规原文和法规 MCP 都在 Git 中。10.4 GB 的旧机本地服务包是可选备份，可以跳过。若要带走在界面里另行修改过的提示词或工具设置，额外复制最新导出的智能体 JSON。以下命令在 PowerShell 执行。首次使用先克隆项目。

```powershell
git clone https://github.com/sanyou1997/Nexent-Test.git
cd Nexent-Test
python -X utf8 restore.py prepare
```

准备命令将校验过版本的上游源码放在 `.local/nexent`，应用覆盖文件，校验九份法规原件及其索引。打开 `.local/nexent/deploy/env/.env`，按上游部署说明配置这台机器的基础设施凭据、访问地址及端口。先启动 Docker Desktop，再执行：

```powershell
python -X utf8 restore.py start
```

首次启动会部署基础服务，构建本项目的前端和后端镜像，再启动法规 MCP。工作台默认在 [本机 Nexent](http://127.0.0.1:3000)。本流程面向干净的测试机，默认 Compose 项目名为 `nexent`，服务使用固定容器名。如果这台机器已经运行另一个 Nexent 实例，先确认更新的是哪一套部署。

### 恢复账号内的配置

1. 在新系统注册并登录目标账号。在“模型配置”添加新环境可用的对话模型和向量模型，分别测试连通。对话模型供智能体调用，向量模型用于建立法规知识库。按需要在“文件解析”配置本机或内网的 OCR、ASR 服务。

2. 在“知识库配置”创建 **纪检审理本地法规库**，选择刚配置的向量模型。上传 [入库文档目录](review/本地法规库/入库文档) 中全部 **9 个 TXT**，等待全部处理完成。文件名和原始字节用于核验，请保留原版。

3. 在仓库根目录生成该账号的智能体导入文件。程序逐份验证目标账号可读取的原件，写入该系统的法规原件位置和检索索引，供站内定位、标黄使用。若知识库改了名字，在命令后追加 `--kb "新名称"`。

   ```powershell
   $account = Read-Host "新系统的账号邮箱"
   python -X utf8 restore.py agent --account $account
   ```

   如果带了最新的智能体导出 JSON，可将上一条 Python 命令替换为下面这条。两种方式都在目标系统的导入向导里选择对话模型，推理参数使用新环境的设置。

   ```powershell
   python -X utf8 restore.py agent --account $account --agent-export "D:/迁移/纪委案件审理辅助.json"
   ```

4. 在“MCP 仓库”添加 SSE 服务，名称填 **review_rules**，地址填 `http://review-rules:8096/sse`，刷新后应看见 `get_rule` 和 `review_report`。在“智能体配置”导入 `.local/agent.json`。在导入向导选择新电脑的对话模型，确认知识库和两项 MCP 工具均选中，在目标租户将分类标签设为 **纪检监察**，再发布“纪委案件审理辅助”。首次导入使用原始智能体名称。
5. 绑定已发布版本，让审理流程使用本账号的法规 MCP。模型和文件解析设置由新系统管理。

   ```powershell
   python -X utf8 restore.py bind --account $account
   ```

6. 先将 [购物卡案例](examples/购物卡案.txt) 的全文粘贴到智能体对话，检查审理意见及法规链接能否定位并标黄。需要审理 PDF 时，在“文件解析”验证目标服务并为该智能体启用 PDF 解析，再上传 [8 页工程结算样例](examples/02_工程变更结算与关联服务转款.pdf)，输入“请帮我审理下这份报告并出具意见”。默认显示总览、审理结论、引用核查、办理建议。检查流水去重为 6 笔、66,000 元，扣除 5,400 元退款后净流入 60,600 元。点击法规链接，应在站内原文中定位并标黄。需要逐笔来源时，随报告明确要求“逐笔核验明细”。

### 可选备份：复用旧电脑的本地推理服务

仅在需要复用旧电脑的 5 个 OCR、ASR 和向量服务镜像时，才单独复制 `Nexent-Local-Services-20261123-linux-amd64.zip`。仓库附有逐文件 SHA-256 清单和导入程序。在项目根目录执行以下命令，路径换成实际位置。

```powershell
python -X utf8 offline/import_bundle.py "D:/迁移/Nexent-Local-Services-20261123-linux-amd64.zip"
docker compose -p nexent-models -f restore/local-services.yaml up -d --pull never
```

导入程序校验全部文件，将权重和镜像归档写入 `.local/offline/20261123/`，并向 Docker 导入缺少的镜像。同标签已存在其他镜像时会提示冲突。已有同一套服务运行的电脑只需核验压缩包，可在第一条命令末尾加 `--verify-only`。

第二条命令适用于可将 NVIDIA GPU 分配给 Linux 容器的 x86-64 电脑，启动 MinerU、BGE-M3 和 Paraformer（含 FSMN-VAD、CT-Punc），宿主机端口分别为 18001、11435、18095。仅运行 CPU 语音服务时，在命令末尾加 `paraformer`。包内也有 Qwen3-ASR 和 Umi-OCR 镜像，按下方各自说明启用 Compose 服务即可跳过单独的镜像导入步骤。DeepSeek 和视觉模型沿用客户可访问的推理接口，需在新租户填写地址和凭据。Nexent 基础镜像及首次构建依赖按上方安装步骤获取。

旧环境示例中的 MinerU、Paraformer ASR 使用宿主机端口 18001、18095。目标电脑需部署相应服务，或在“文件解析”中选用可访问的内网提供方。使用其他地址时，在目标部署中同步配置相应服务允许访问的来源。项目提供 Qwen ASR 适配和服务源码，另附 [Qwen ASR 部署说明](offline/Qwen3-ASR本地部署.md) 和 [Umi-OCR 部署说明](offline/Umi-OCR本地部署.md)。使用本次配套 ZIP 可一次转移这些本地服务的镜像与权重，校验文件在 `offline/`。

## 已安装电脑更新

在本项目根目录运行：

```powershell
git pull --ff-only
python -X utf8 restore.py start
```

该命令重新应用覆盖文件并更新本项目服务。已生成的 `.env`、租户映射和法规文件地址保存在该电脑的 `.local/`，数据库与索引保存在相应的本机持久化存储。日常更新同一账号无需重复导入智能体。若九份法规文件发生变化，应在知识库更新对应原件、完成索引并重新运行 `agent`。若智能体、模型或知识库被重建，应重新核对目标绑定并发布。

## 数据库与账号的关系

单独导出的智能体 JSON 可以备份界面里的智能体配置。法条原件、检索索引、引用预览所用的对象地址、MCP 服务程序和模型权重分别保存在其他位置。迁移本项目时先恢复原件和服务，再生成目标租户的智能体 JSON。导入后的最后一步是发布并运行 `bind`。如果只更新同一部署的代码，原有账号和数据卷可以继续使用。

| 内容 | 随 Git 交付 | 还原位置 |
| --- | --- | --- |
| 系统改动、数据库迁移、测试用例 | `overlay/` 和固定上游版本 | `.local/nexent` |
| 对话模型、向量模型、推理参数、文件解析 | 在新机按现场环境配置 | 新账号的“模型配置”和“文件解析”；常规迁移跳过旧环境的 `seed` 命令 |
| 智能体提示词、工具、开场白 | `review/agent.template.json` | 按目标 ID 生成后在平台导入并发布 |
| 法规快照、条文和文件校验值 | `review/本地法规库` | 目标 MinIO 原件、Elasticsearch 索引及本地法规 MCP |
| 验收材料 | `examples/` | 新账号可重新发起审理 |
| 密码、令牌、接口密钥、聊天记录、其余租户和 Docker 数据卷 | 仅在原部署 | 若需要原样迁机，另行私下备份业务 PostgreSQL、Supabase Auth、MinIO、搜索索引和部署密钥 |

每台 Nexent 的用户、租户、模型、智能体和知识库均有自己的标识；使用与旧系统相同的邮箱也要重新绑定。本恢复包为单个目标测试租户提供专用法规 MCP。多个租户分别配置各自的原件映射与 MCP 实例。`.local/` 含目标环境私有配置，Git 已忽略。

法规快照记录的是归档当日的来源、版本和施行日期。源文件哈希用于确认两台机器使用相同原文。法规时效需要随业务知识库维护。
