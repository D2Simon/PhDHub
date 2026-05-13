# PhDHub

PhDHub 是一个面向博士申请场景的本地化 AI 工作台，把“简历/RP 管理、邮件识别与分类、导师建库、面试准备与复盘”串成一个闭环，帮助申请者减少信息分散和跟进遗漏。

## 界面轮播预览

![PhDHub UI Carousel](fig/carousel.gif)

## 项目具体功能

- 简历管理（My Resume）
  - 支持多份 PDF 简历上传、切换、删除、缩略图预览。
  - AI 自动生成简历分析（优势/劣势/改进建议），并按简历维度缓存。

- RP 管理（My RP）
  - 支持多份 RP PDF 上传、切换、删除与预览。
  - AI 自动输出 RP 优点、缺点、改进建议。

- 智能邮箱中心（AI Email）
  - IMAP 拉取邮件、缓存读取、手动强制拉取。
  - 自动识别博士申请相关邮件并分类（已发送/积极回复/中立/消极/面试等）。
  - 从邮件 + 导师主页中提取导师档案，快速同步到导师库。

- 套瓷进度大盘（Dashboard）
  - 展示近 7 天与累计指标（发送、回复、积极/中立/消极、面试预约）。
  - 提供阶段化进度追踪与可视化。

- 导师库管理（Professor DB）
  - 统一管理导师/学校/院系/国家/研究方向/阶段/更新时间。
  - 支持筛选、时区展示、记录维护。

- 面试准备舱（Interview Prep）
  - 一键生成高频面试问题。
  - 基于简历 + 导师主页 + 论文信息生成个性化面试建议。
  - 模拟面试对话、追问与评分复盘。
  - 支持“高频考察点”沉淀（问题 + 建议回答 + 要点）。

- 系统配置（System Config）
  - 支持 Qwen / Gemini 模型切换与 API Key 自动保存。
  - 支持邮箱连接配置（IMAP/SMTP）。

## 安装依赖教程

> 建议 Python 3.10+（最低建议 3.9）。

1. 克隆项目

```bash
git clone <你的仓库地址>
cd PhDHub
```

2. 创建并激活虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
```

3. 安装依赖

```bash
pip install -U pip
pip install -r requirements.txt
```

4. 启动应用

```bash
bash run.sh
```

## Docker Compose 启动

如果你已安装 Docker / Docker Compose，也可以直接容器化运行：

```bash
docker compose up -d --build
```

启动后访问：http://localhost:8501

常用命令：

```bash
# 停止服务（保留数据卷）
docker compose down
```

应用数据默认保存在 Docker volume `phdhub_data`（容器内 `/data`）。如需清空数据，可执行 `docker compose down -v`。

## 配置说明

首次启动后，在 `系统配置 / System Config` 页面填写：

- 邮箱账号、IMAP/SMTP、应用专用密码（推荐 App Password）
- AI 提供商（Qwen 或 Gemini）及对应 API Key

---

作者主页: https://d2simon.github.io/
研究方向：计算机视觉

作者也在申请博士中，如果你正在招收 26-27 届博士，期待我们建立联系。如果有任何你觉得实用的功能或者遇到什么 bug，可以在 issue 中提出。

Author Homepage: https://d2simon.github.io/
Research Direction: Computer Vision.

I am also applying for PhD programs. If you are recruiting PhD students for the 2026-2027 intake, I would be glad to connect with you. If you find any useful feature ideas or run into bugs, please open an issue.

希望大家都能拿到心仪的 PhD offer。
Wishing everyone the best in getting their ideal PhD offer.
