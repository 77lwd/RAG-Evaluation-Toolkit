# RAG Evaluation Toolkit

面向团队的 RAG 测评治理工具包，帮助配置多个系统版本、登记测评材料、检查准备情况并保存可复核的记录。系统 ID、版本和 comparison arm ID 均由使用团队配置，不限定为 A/B。

工具包不会替团队运行模型，也不会认证人员身份、证明真实访问隔离或授权正式测评。示例和 quickstart 只使用合成材料。

## 安装

从 GitHub 克隆仓库后，在仓库根目录创建独立 Python 环境并安装：

```powershell
git clone https://github.com/77lwd/RAG-Evaluation-Toolkit.git
cd RAG-Evaluation-Toolkit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
rag-evaluation --help
```

macOS/Linux 激活环境时使用 `source .venv/bin/activate`。也可以不激活环境，直接调用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe -m rag_evaluation --help
```

需要 Python 3.10 或更高版本。依赖与代码安装在虚拟环境中，不会添加到业务项目依赖里。

## 快速验证

运行不访问模型、不读取团队数据的合成示例：

```powershell
python -m examples.quickstart
```

运行测试：

```powershell
python -m unittest discover -s tests -p 'test_rag_evaluation_*.py' -v
```

## 团队试用

1. 复制并修改 [`examples/inventory-config.example.json`](examples/inventory-config.example.json)，为每个系统和候选版本填写团队自己的身份、源码目录、路径白名单和 arm ID。
2. 运行 `inventory-configured` 生成材料清单与比较包。
3. 初始化 registry，登记并复核比较方案。
4. 使用 `draft-readiness` 生成准备度草稿，补齐真实负责人、独立 reviewer 和证据引用后，再登记并检查。

可复制的完整命令、Skill 安装方式和参数说明见 [`docs/evaluation/INSTALLATION.md`](docs/evaluation/INSTALLATION.md)。Codex Skill 位于 [`skills/rag-evaluation/SKILL.md`](skills/rag-evaluation/SKILL.md)。

不要把真实评测数据、受限证据、团队源码或凭据提交到此仓库。证据应保存在团队批准的位置，命令只绑定必要的本地证据根目录。

## 项目状态

仓库支持从源码克隆后安装和试用；尚未发布 PyPI 包、带版本标签的发行版或升级迁移流程。测评角色和必需证据类别来自当前工具内置的 readiness profile，组织应先确认它适用于自身治理要求。

本仓库目前没有声明许可证。公开可见不等于授予复制、修改或分发权限；在增加许可证前，请先取得仓库所有者的授权。
