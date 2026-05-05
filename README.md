# C28x Reverse Agent

`C28x Reverse Agent` 是一个面向 **TI C28x / C2000** 逆向分析场景的本地 Agent 项目。  
它基于 **LangGraph** 编排流程，通过 **Textual TUI** 提供本地交互界面，支持使用 **IDA Pro MCP** 或本地导出的汇编 / listing / 文本材料作为输入来源，对指定函数或入口调用链进行还原，并在授权范围内操作本地文件。


## 主要功能

- 支持 **本地模型** 和 **线上模型**
- 支持通过 **IDA Pro MCP** 直接获取函数、反汇编、交叉引用和上下文
- 支持通过 **ASM / listing / 导出文本** 作为输入材料进行还原
- 支持按 **单函数** 或 **入口调用链** 两种模式工作
- 支持在 `authorized_paths` / `source_files` 范围内安全读写文件
- 提供本地 **Textual TUI** 作为默认使用界面


## 安装

### 环境要求

- Python `>=3.11,<4.0`

### 安装依赖

项目根目录下提供了 `requirements.txt`，可以直接一键安装：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

如果你已经有自己的 Python 环境，也可以只执行：

```powershell
pip install -r requirements.txt
pip install -e .
```


## 配置

项目通过 `.env` 管理运行时配置。

### 模型配置

当前支持两类模型配置：

- `LLM_PROFILE=openai`
  - 使用线上模型或 OpenAI 兼容接口
- `LLM_PROFILE=local`
  - 使用本地模型

示例：

```dotenv
LLM_PROFILE=local

OPENAI_MODEL=openai/gpt-5.4
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-openai-key

LOCAL_MODEL=openai/your-local-model
LOCAL_BASE_URL=http://127.0.0.1:11434/v1
LOCAL_API_KEY=ollama
```

### MCP 配置

如果使用 IDA Pro MCP，可在 `.env` 中配置：

```dotenv
MCP_ENABLED=true
MCP_SERVER_NAME=ida-pro-mcp
MCP_TRANSPORT=http
MCP_URL=http://127.0.0.1:13337/mcp
```

如果不使用 MCP，也可以仅通过本地汇编文件、listing 或导出文本驱动 Agent。

### 可选联网搜索

如果需要联网搜索能力，可以配置：

```dotenv
TAVILY_API_KEY=your-tavily-key
```


## 启动方式

默认使用 Textual TUI：

```powershell
python tui_app.py --inline
```

常用快捷键：

- `Enter`：发送
- `Ctrl+L`：清空当前 UI
- `Ctrl+C`：退出


## 文件操作安全边界

Agent 当前具备文件工具，但文件读写改都限制在用户明确授权的范围内。

### `authorized_paths`

表示用户明确授权 Agent 可操作的目录。

### `source_files`

表示用户明确授权 Agent 可操作的文件。

### 当前文件工具

- `list_directory`
- `read_file`
- `create_directory`
- `write_file`
- `replace_in_file`

### 当前限制

- 只在 `authorized_paths` / `source_files` 范围内暴露文件能力
- 拦截部分敏感目录，例如：
  - `.git`
  - `.venv`
  - `node_modules`
  - `__pycache__`


## 运行形态

当前项目更适合作为一个 **本地逆向工作台 Agent** 使用：

- TUI 负责本地交互
- LangGraph 负责状态流转和节点编排
- MCP / 文件 / 搜索工具负责外部能力接入

当前会话在同一次 TUI 运行期间保留短期记忆，便于连续推进还原任务。


## 项目中与使用最相关的文件

- [tui_app.py](D:/workEnvironment/ai/Agent/C28Reverse-agent/tui_app.py)
  - Textual 本地界面入口

- [src/react_agent/graph.py](D:/workEnvironment/ai/Agent/C28Reverse-agent/src/react_agent/graph.py)
  - LangGraph 主流程

- [src/react_agent/context.py](D:/workEnvironment/ai/Agent/C28Reverse-agent/src/react_agent/context.py)
  - 运行时模型、MCP 和环境配置

- [src/react_agent/tools.py](D:/workEnvironment/ai/Agent/C28Reverse-agent/src/react_agent/tools.py)
  - 搜索工具、MCP 工具、文件工具的运行时装配

- [src/react_agent/file_tools.py](D:/workEnvironment/ai/Agent/C28Reverse-agent/src/react_agent/file_tools.py)
  - 本地文件读写改工具及授权限制


## 说明

当前 README 只保留项目介绍、安装方式、基础配置和能力边界说明。  
后续如果你给出完整业务流程，我可以再补对应的 **Mermaid 流程图**，把 Agent 的实际工作链路整理进去。
