# Nexent 本地部署与纪委案件审理辅助

在 Nexent v2.8.5 基础上提供按租户配置的本地文件解析、审理意见四段式输出、法规原件预览和引用标黄。项目以覆盖文件配合固定的上游版本发布。首次恢复需要 Git、Python 3.10+、Docker Compose v2，以及拉取 GitHub、Docker 镜像和构建依赖的网络。Windows 使用 Docker Desktop 的 Linux 容器和 Git for Windows，并预留 Nexent 服务端口。

## 新电脑首次安装

以下命令在 PowerShell 执行。首次使用先克隆项目。

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

1. 在新系统注册目标管理员，登录一次。设定目标邮箱并导入 PostgreSQL 配置种子。种子会补齐模型目录和租户文件解析默认设置，重复运行保留已有记录。

   ```powershell
   $account = Read-Host "新系统的管理员邮箱"
   python -X utf8 restore.py seed --account $account
   ```

2. 在“模型配置”中给 `deepseek-flash`、`bge-m3` 和需要使用的视觉模型 `qwen3.8-27b` 填写本地实际接口和所需凭据，测试连通后设置默认语言、向量和视觉模型。服务可部署在本机或内网的另一台主机。切换向量模型后需按新模型重新建索引。
3. 在“知识库配置”创建 **纪检审理本地法规库**，选择可用的 `bge-m3`。上传 [入库文档目录](review/本地法规库/入库文档) 中全部 **9 个 TXT**，等待各文件处理完成。文件名和原始字节用于核验，请保留原版。若知识库改名，下一条命令追加 `--kb "新名称"`。
4. 生成该租户专用的智能体导入文件。程序会逐份读取目标账号可访问的原件，验证 SHA-256，取得该系统实际的对象地址、模型 ID 和索引名。

   ```powershell
   python -X utf8 restore.py agent --account $account
   ```

5. 在“MCP 仓库”添加 SSE 服务，名称填 **review_rules**，地址填 `http://review-rules:8096/sse`，刷新后应看见 `get_rule` 和 `review_report`。在“智能体配置”导入 `.local/agent.json`。确认模型、知识库和两项 MCP 工具均选中，再发布“纪委案件审理辅助”。首次导入使用原始智能体名称。
6. 绑定已发布版本并启用该智能体的文件解析。到图形界面的“文件解析”核对本机服务地址并测试连接。

   ```powershell
   python -X utf8 restore.py bind --account $account
   ```

7. 在智能体对话中上传 [8 页工程结算样例](examples/02_工程变更结算与关联服务转款.pdf)，输入“请帮我审理下这份报告并出具意见”。默认显示总览、审理结论、引用核查、办理建议。检查流水去重为 6 笔、66,000 元，扣除 5,400 元退款后净流入 60,600 元。点击法规链接，应在站内原文中定位并标黄。需要逐笔来源时，随报告明确要求“逐笔核验明细”。

### 本地 OCR、ASR 和模型服务

种子中的 MinerU、Paraformer ASR 地址使用 Docker 容器访问宿主机的示例端口 18001、18095。目标电脑需部署相应服务，或在“文件解析”中选用可访问的内网提供方。使用其他地址时，在目标部署中同步配置相应服务允许访问的来源。项目提供 Qwen ASR 适配和服务源码，另附 [Qwen ASR 部署说明](offline/Qwen3-ASR本地部署.md) 和 [Umi-OCR 部署说明](offline/Umi-OCR本地部署.md)。离线部署需另行转移模型权重、OCR/ASR 镜像和相应依赖，校验文件在 `offline/`。

## 已安装电脑更新

在本项目根目录运行：

```powershell
git pull --ff-only
python -X utf8 restore.py start
```

该命令重新应用覆盖文件并更新本项目服务。已生成的 `.env`、租户映射和法规文件地址保存在该电脑的 `.local/`，数据库与索引保存在相应的本机持久化存储。日常更新同一账号无需重复导入智能体。若九份法规文件发生变化，应在知识库更新对应原件、完成索引并重新运行 `agent`。若智能体、模型或知识库被重建，应重新核对目标绑定并发布。

## 数据库与账号的关系

| 内容 | 随 Git 交付 | 还原位置 |
| --- | --- | --- |
| 系统改动、数据库迁移、测试用例 | `overlay/` 和固定上游版本 | `.local/nexent` |
| 模型参数、文件解析设置 | `database/test-seed.sql`、`review/models.json`、`review/parser.json` | 新账号所属租户的 PostgreSQL 记录 |
| 智能体提示词、模型覆盖参数、工具、开场白 | `review/agent.template.json` | 按目标 ID 生成后在平台导入并发布 |
| 法规快照、条文和文件校验值 | `review/本地法规库` | 目标 MinIO 原件、Elasticsearch 索引及本地法规 MCP |
| 验收材料 | `examples/` | 新账号可重新发起审理 |
| 密码、令牌、接口密钥、聊天记录、其余租户和 Docker 数据卷 | 仅在原部署 | 若需要原样迁机，另行私下备份业务 PostgreSQL、Supabase Auth、MinIO、搜索索引和部署密钥 |

每台 Nexent 的用户、租户、模型、智能体和知识库均有自己的标识；使用与旧系统相同的邮箱也要重新绑定。本恢复包为单个目标测试租户提供专用法规 MCP。多个租户分别配置各自的原件映射与 MCP 实例。`.local/` 含目标环境私有配置，Git 已忽略。

法规快照记录的是归档当日的来源、版本和施行日期。源文件哈希用于确认两台机器使用相同原文。法规时效需要随业务知识库维护。
