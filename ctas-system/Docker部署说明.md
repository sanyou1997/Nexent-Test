# 纪检监察业务辅助平台 · Docker 部署说明

平台以容器方式与已部署的 Nexent 同机运行，单应用、单库：

| 容器 | 作用 | 端口 / 网络 |
|---|---|---|
| `ctas` | 业务平台（FastAPI + 前端），全部业务模块 | 宿主 `127.0.0.1:8090`；`default` + `nexent_network` + `nexent_local_ocr` |
| `ctas-db` | PostgreSQL 15，业务数据库 | 仅容器网络 |
| `ctas-mcp` | 业务平台 MCP 服务 `ctas_tools`：把案件 / 信访 / 审理查询与量纪、证据链、程序合规、法规条文、类案检索开放给 Nexent 智能体（只读，令牌鉴权） | 仅容器网络（`http://ctas-mcp:8097/sse`） |

与 Nexent 的关系（**平台不做模型路由，大模型能力全部复用 Nexent**）：

- **场景智能体**：平台内所有生成 / 抽取 / 审查类调用按 6 个场景交给 Nexent 智能体（要素抽取、文书起草、文书审查、法规问答、会议纪要、研判分析）。用哪个模型、挂哪些知识库和工具，在 Nexent 里给智能体配置；开发环境用线上 API，部署到内网后只需在 Nexent 里把智能体的模型换成本地模型，平台无需改动。
- **知识库**：平台法规库是主本（结构化到条），单向同步为 Nexent 知识库「纪检监察业务平台法规库」，场景智能体检索同一份语料；精确条文由 MCP 工具 `get_regulation_article` 从主本取。
- **业务工具**：`ctas-mcp` 注册为 Nexent 的 MCP 服务，「纪检研判助手」等智能体可直接查案件、跑量纪说理与证据链校验。
- **识别服务**：OCR、扫描版 PDF、谈话录音复用 Nexent 的 Umi-OCR / MinerU / Paraformer / Qwen3-ASR，默认跟随 Nexent 租户「文件解析」配置。

## 一、前置条件

1. Nexent 已用 Docker 部署并在运行（`docker network ls` 能看到 `nexent_network`、`nexent_local_ocr`）。
2. 前端已构建并复制到 `backend/web/`（仓库里已是最新产物；改了前端代码才需要重新构建）：

```powershell
cd ctas-system\frontend
npm run build
Remove-Item -Recurse -Force ..\backend\web; Copy-Item -Recurse dist ..\backend\web
```

   宿主机没有 Node.js 时用容器构建（依赖装在独立卷 `ctas-fe-node-modules`，不动宿主机 `node_modules`，产物自动同步到 `backend/web`）：`sh scripts/build_frontend.sh`

3. 在 `ctas-system/` 下由 `.env.docker.example` 复制出 `.env`，至少设置 `CTAS_SECRET_KEY`、`CTAS_DB_PASSWORD`、`CTAS_MCP_TOKEN`（均为随机长字符串）。

## 二、构建与启动

```powershell
cd ctas-system
docker compose up -d --build
```

- 访问 `http://127.0.0.1:8090`（避开 Higress 控制台与内网方案中 vLLM 模型服务占用的 8001–8005；可用 `.env` 的 `CTAS_PORT` 修改）。允许内网其他机器访问时，在 `.env` 中设 `CTAS_BIND=0.0.0.0`。
- 首次启动自动建表并加载演示数据（20 部法规 / 1535 条、演示案件与审理事项、历史报告语料），之后幂等不重复加载。
- 国内或内网构建可加镜像源：`.env` 中设 `CTAS_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`。
- 开发调试需要直连数据库时：`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`（PostgreSQL 映射到 `127.0.0.1:15432`）。

常用命令：

```powershell
docker compose logs -f ctas          # 查看日志
docker compose restart ctas          # 重启
docker compose down                  # 停止（数据卷保留）
sh scripts/run_checks.sh             # 全模块功能检查 + 审理引擎自检（一次性克隆库上运行，见第六节）
```

## 三、接入 Nexent（管理员，首次部署做一次）

以下 `<邮箱>` 为 Nexent 中放置业务智能体的租户管理员账号。脚本均在 `nexent-config` 容器内运行，调用 Nexent 自身的服务函数，幂等可重复执行。Windows Git Bash 下在命令前加 `MSYS_NO_PATHCONV=1`。

1. **API Key**：Nexent「个人信息 → 生成 API 密钥」生成后，在平台「系统管理 → Nexent 接入」粘贴 → 测试连接 → 保存。全系统共用一个 Key，平台按用户登记会话归属。
2. **法规库同步到 Nexent 知识库**（平台法规库有更新时重跑，增量：只重传有变化的法规，下架的自动删除）：

```sh
sh docker/nexent/sync_regulations_kb.sh <邮箱>
```

   脚本结束时把同步记录回写到平台；「法规库」页顶部的「Nexent 知识库联动」据此显示：已同步 / 有更新待同步 / 未同步 / 已下架待移除，以及最近同步时间。平台新增或修改法规后会立即显示为「待同步」，提醒运维重跑本命令。智能体按条号取条文（MCP `get_regulation_article`）直接读平台主本，不受同步延迟影响。

3. **注册业务平台 MCP 服务**（令牌与 `.env` 中 `CTAS_MCP_TOKEN` 一致；平台新增工具后重跑即可刷新工具清单）：

```sh
docker cp docker/nexent/register_ctas_mcp.py nexent-config:/tmp/
docker exec -w /opt/backend -e PYTHONPATH=/opt/backend -e CTAS_MCP_TOKEN=<令牌> \
  nexent-config python /tmp/register_ctas_mcp.py --email <邮箱>
```

4. **安装 / 更新场景智能体**（ctas_extract、ctas_writer、ctas_law_qa、ctas_analyst；绑定上一步的知识库与 MCP 工具并发布）：

```sh
docker cp docker/nexent/install_scene_agents.py nexent-config:/tmp/
docker exec -w /opt/backend -e PYTHONPATH=/opt/backend nexent-config \
  python /tmp/install_scene_agents.py --email <邮箱>
```

   安装后在平台「Nexent 接入 → 场景智能体」逐个点「实测」确认。模型取租户默认 LLM，可在 Nexent 中按智能体单独改。

5. **识别服务「跟随 Nexent」**（可选，未配置时按手动配置运行）：Nexent 的识别配置接口只接受网页登录，平台通过一个只读数据库账号读取。由 Nexent 数据库管理员执行一次：

```powershell
Get-Content docker/nexent-readonly-role.sql -Raw | docker exec -i nexent-postgresql psql -U root -d nexent -v pw=<强密码>
```

   再在 `.env` 中加入 `NEXENT_DB_DSN=postgresql://ctas_reader:<强密码>@nexent-postgresql:5432/nexent`，执行 `docker compose up -d`。该账号只能读 `nexent.document_parser_settings`，并通过函数按 API Key 查所属租户。

## 四、部门（租户）与权限

平台按部门划分租户，由平台管理员在「系统管理 → 部门与权限」维护：

- **功能模块**：每个部门开通哪些工作面（导航与接口同时生效，未开通模块的接口返回 403）。工作台对所有部门开放；系统管理仅平台管理员。法规库、法规问答、历史报告库、智能体中心为通用能力，数据全平台共享。
- **数据范围**：本部门 / 本部门 + 指定部门 / 全部部门。业务数据（信访、线索案件、审理、党风、巡察、监督模型与预警、资金、廉政档案、材料与文书、智能体会话等）按部门归属，新建时自动归经办人部门。平台管理员不受限。
- **跨部门移交**：信访转线索、移送审理、党风 / 巡察移交时，新记录归承办部门（多个部门可承办时由经办人选择），并自动授权——承办部门可查阅来源记录，来源部门可跟踪办理结果；只对这一条记录生效。
- **账号**：每个账号归属一个部门；角色（平台管理员 / 信访岗 / 办案岗 / 审理岗）决定模块内可办理的操作。部门停用后该部门账号不能登录。
- **升级**：首次启动自动建立默认部门（系统管理部门 / 信访室 / 审查调查室 / 案件审理室），按角色把已有账号归入部门，按经办人与业务归口给已有数据回填归属，并补齐历史移交的授权（幂等）。
- **MCP 部门令牌**：在部门配置中生成，注册到该部门对应的 Nexent 租户后，智能体经 MCP 只能查到该部门数据范围内的数据。`.env` 中的 `CTAS_MCP_TOKEN` 是平台级令牌（全部部门），只适合单租户部署。

隔离在 ORM 层统一施加（`app/tenancy.py`），覆盖列表、详情、统计、批量更新 / 删除；`tenancy_check.py` 验证模块把关、数据隔离、移交授权、数据范围配置与 MCP 部门令牌。

## 五、开发环境 → 内网部署

| 项 | 开发环境（当前） | 内网部署 |
|---|---|---|
| 场景智能体的模型 | Nexent 中的线上 API 模型 | 在 Nexent 里把 4 个场景智能体的模型改为本地模型（如 910B4 上的 vLLM），平台不改 |
| 知识库向量模型 | Nexent 租户 Embedding 配置 | 知识库的向量模型在创建时固定，增量同步会因内容未变全部跳过。换模型后：① 在 Nexent 切换租户 Embedding；② 同步到一个**新**知识库 `sync_regulations_kb.sh <邮箱> 纪检监察业务平台法规库v2`；③ `install_scene_agents.py --kb 纪检监察业务平台法规库v2` 改绑场景智能体；④ 确认无误后在 Nexent 删除旧库 |
| 识别服务 | 跟随 Nexent | 跟随 Nexent（Nexent 切到本地 OCR / ASR 即生效） |
| 镜像 | 本机构建 | 联网机器 `docker save ctas-system:2.0 postgres:15-alpine -o ctas.tar`，内网 `docker load -i ctas.tar` 后 `docker compose up -d`（不加 `--build`） |

## 六、数据与备份

| 内容 | 位置 |
|---|---|
| 业务数据库 | Docker 卷 `ctas_ctas-pgdata`（PostgreSQL） |
| 上传原件、档案附件、录音，Nexent 接入 / 识别服务设置 | Docker 卷 `ctas_ctas-data`（容器内 `/app/data`） |
| 法规语料、演示夹具 | 镜像内 `/app/data/corpus`、`/app/app/fixtures` |

备份：

```powershell
docker exec ctas-db pg_dump -U ctas -Fc -f /tmp/ctas-db.dump ctas
docker cp ctas-db:/tmp/ctas-db.dump .
docker run --rm -v ctas_ctas-data:/data -v ${PWD}:/backup alpine tar czf /backup/ctas-data.tgz -C /data .
```

恢复数据库（先在容器内转储，避免 PowerShell 重定向损坏二进制文件）：

```powershell
docker cp ctas-db.dump ctas-db:/tmp/ctas-db.dump
docker exec ctas-db pg_restore -U ctas -d ctas --clean --if-exists /tmp/ctas-db.dump
```

## 七、功能检查

`sh scripts/run_checks.sh` 会：在 `ctas-db` 内把业务库克隆为一次性的 `ctas_check` → 拷入 `testdata/`（客户提供的各模块仿真样本）→ 运行 `platform_check.py`（全模块，真实调用 OCR / ASR / Nexent 场景智能体）、`ctas_selfcheck.py`（审理引擎 107 项）与 `tenancy_check.py`（部门隔离）→ 删除克隆库。结果写入 `scripts/platform_check.json`。三个脚本都拒绝在业务库 `ctas` 上直接运行。

## 八、注意

- 涉密材料只能发往内网部署、使用本地模型的 Nexent 与识别服务；开发环境的线上 API 只用于仿真数据。
- `ctas-mcp` 不映射宿主端口，仅 Nexent 容器网络可达；令牌泄露时在 `.env` 中更换 `CTAS_MCP_TOKEN`，`docker compose up -d` 后重跑第三节第 3 步。
- MCP 工具只读、只返回摘要级数据（不含原件与附件），每次调用写入平台审计日志（操作人 `nexent-mcp`）。


## 九、在已有 Nexent 的环境上部署 / 升级（git pull 之后）

**一条命令（推荐）**：部署机上，在 Nexent-Test 根目录执行：

```sh
git pull --ff-only
sh ctas-system/scripts/deploy.sh <Nexent 账号邮箱>     # 首次；邮箱记入 .env，以后升级可省略
```

脚本可重复执行，已完成的步骤自动跳过，下面第 1–6、8 步中能自动化的部分都由它完成：
- 检查 Docker、Nexent 容器与网络；
- 首次生成 `.env`（随机密钥，`CTAS_BIND=0.0.0.0`）；
- 升级前把业务库备份到 `backups/`（保留最近 10 份）；
- 构建并启动；
- 接入 Nexent：
  - 把该账号已有的 API Key 写入平台，没有才新建；Key 不显示，也不会作废该账号的其他 Key；
  - 关闭该账号的智能体记忆；
  - 建识别服务只读账号；
  - 同步法规知识库，注册 MCP，安装场景智能体。

接入在 `nexent-config` 容器内按邮箱调用 Nexent 自身的服务函数，不需要账号密码。该账号须已在 Nexent 配好对话模型和向量模型。

- Nexent 覆盖文件有更新时加 `--nexent`，先运行 `restore.py start`；
- 加 `--check` 跑全模块功能检查；
- 加 `--keep-memory` 保留智能体记忆。

脚本完成后只剩人工操作：admin 登录改密码、调整部门与账号（第 7 步）、在「Nexent 接入 → 场景智能体」逐个实测。

以下为手动步骤，用于脚本失败时排查或单独执行某一步。按顺序检查，已做过的步骤跳过即可（脚本均幂等）。

**0. 取得代码**：业务平台随 Nexent 部署仓库 [Nexent-Test](https://github.com/sanyou1997/Nexent-Test) 的 `ctas-system/` 子目录发布。

- 开发机：在本仓库提交后运行 `sh scripts/publish_to_nexent_test.sh`，把已提交的代码同步到本机 Nexent-Test 克隆的 `ctas-system/` 并提交（不带本仓库的提交历史与作者信息），检查后在 Nexent-Test 中 `git push`。
- 部署机：在 Nexent-Test 根目录 `git pull --ff-only`，以下命令都在 `Nexent-Test/ctas-system` 下执行。`.env` 不入库，`git pull` 不会覆盖它。Nexent 本身照 Nexent-Test 的 README 用 `restore.py start` 更新，两者互不影响（业务平台的 Compose 项目名固定为 `ctas`）。

**1. 前置确认**

| 检查 | 命令 | 不满足时 |
|---|---|---|
| Nexent 为 2.8.5（`docker/nexent/*.py` 调用的是 2.8.5 的内部模块） | `docker inspect nexent-config -f '{{.Config.Image}}'` | 其他版本先在测试机跑通第三节脚本 |
| 网络 `nexent_network` 存在 | `docker network ls` | Nexent 未启动或不是 Docker 部署 |
| 网络 `nexent_local_ocr` 存在 | 同上 | 这是本地 Umi-OCR / Qwen3-ASR 用的网络，原版 Nexent 没有；没有时先 `docker network create nexent_local_ocr`，否则 compose 启动报错 |
| 构建镜像能访问 PyPI | — | `.env` 设 `CTAS_PIP_INDEX_URL` 为内网源，或在联网机器 `docker save` 后拷入（第五节） |

前端产物 `backend/web/` 已随代码提交，部署机不需要 Node.js。

**2. `.env`**

- 该环境**从未部署过平台**：`cp .env.docker.example .env`，设置 `CTAS_SECRET_KEY`、`CTAS_DB_PASSWORD`、`CTAS_MCP_TOKEN`（各自随机长字符串）；需要内网其他机器访问时设 `CTAS_BIND=0.0.0.0`。
- **已部署过（升级）**：保留原 `.env`，不要重新生成 `CTAS_DB_PASSWORD`（数据库卷里已用旧密码初始化）和 `CTAS_SECRET_KEY`（换了所有人需重新登录）。

**3. 备份（升级时）**：见第六节 `pg_dump`。

**4. 构建并启动**：`docker compose up -d --build`，然后 `docker logs ctas | grep -E "tenancy|demo-data"` 查看启动迁移：
- 新库：建演示账号、法规库（夹具）、示范案件，建立 4 个默认部门；
- 升级：自动补 `dept_id` 列、人员档案 / 预警指纹改为部门内唯一、建默认部门、账号按角色归部门、已有数据回填归属、补齐历史移交授权。法规库不会被夹具覆盖。

新库的演示账号为默认密码（见登录页），用于正式环境前在「用户管理」逐个重置或停用。

**5. 接入 Nexent（该环境首次部署时做；按第三节）**：`<邮箱>` 用该环境 Nexent 中放业务智能体的租户管理员账号。
1. 平台「系统管理 → Nexent 接入」填 API Key，测试并保存；
2. `sh docker/nexent/sync_regulations_kb.sh <邮箱>`（Nexent 租户须已配置 Embedding 模型）；
3. 注册 MCP：`register_ctas_mcp.py`，令牌用 `.env` 的 `CTAS_MCP_TOKEN`；
4. 安装场景智能体：`install_scene_agents.py`，装完在「Nexent 接入 → 场景智能体」逐个实测。

**6. 识别服务**：OCR / 扫描 PDF / 录音转写依赖 Umi-OCR、MinerU、Paraformer / Qwen3-ASR，这些是本地另行部署的服务，不在本仓库。
- 该环境的 Nexent 有「文件解析」设置页（库中有 `nexent.document_parser_settings` 表）时，可按第三节第 5 步配置「跟随 Nexent」；
- 否则在 `.env` 中按实际可用的服务设置 `CTAS_OCR_PROVIDER` / `CTAS_PDF_MODE` / `CTAS_ASR_PROVIDER` 与对应地址，或在「系统管理 → 识别服务」中配置并测试。服务不可用时相关功能会提示失败，其余模块不受影响。

**7. 部门与账号**：管理员登录 →「系统管理 → 部门与权限」按实际机构调整默认部门（名称、模块、数据范围），在「用户管理」建各部门账号（第四节）。Nexent 只有一个租户时，智能体侧（MCP 平台级令牌、智能体记忆）仍可跨部门，需要按部门隔离时为各部门建 Nexent 租户并注册部门 MCP 令牌，或至少关闭场景智能体记忆。

**8. 验证**：`sh scripts/run_checks.sh`。`tenancy_check`（部门隔离）与 `ctas_selfcheck`（审理引擎）不依赖外部服务，应全部通过；`platform_check` 会真实调用 OCR / ASR / Nexent，未部署的识别服务对应项显示「部分可用 / 不可用」属预期。
