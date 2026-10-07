# 仿真 CLI 应用

在 Apollo 开发容器中，先加载环境，再从任意目录直接调用：

```bash
source /apollo_workspace/modules/simulation/setup.bash
gen_binary.py --help
gen_binary.py --type simulation
gen_binary.py --type all --output /apollo_workspace/binary-all.tar.gz
```

将 `/apollo_workspace` 替换为实际工作区路径。常用的 Apollo 环境也会自动加载
这些命令，例如 `source /apollo_workspace/cyber/setup.bash`。
仿真环境只设置 `SIMULATION_ROOT_DIR` 和 PATH，不切换工作目录。
可以重复 source，不会重复添加目录。无需 pip 安装、Docker 转发或命令注册。

## 目录和流程

```text
modules/simulation/
  setup.bash                    # 将 apps 加入 PATH
  apps/
    _cli.py                     # 工作区定位、业务模块导入、错误和退出码
    gen_binary.py               # 参数解析、校验及调用业务实现
    gen_simulation_task.py      # 解析场景/Binary，stdout 输出任务 JSON
    launch.py                   # 接收 JSON，选择运行后端并执行
  tools/
    package/
      binary_packager.py        # 打包业务逻辑
      binary_profiles.json      # 可扩展打包类型
      test_gen_binary.py
    execution/                  # Binary、任务协议和执行后端
```

每个应用统一遵循：`make_parser → parse_args → validate_args → generate → 返回 0`。
使用 `run_cli(main)` 作为最终入口：成功退出 0，参数错误由 argparse 退出 2，
预期业务错误输出到 stderr 并退出 1，Ctrl+C 退出 130。未预期的代码错误保留
traceback，便于排查。`_cli.py` 根据自身位置找到工作区并提供业务模块导入路径，
无需依赖当前目录或全局 PYTHONPATH。

`gen_binary.py` 默认产物始终位于工作区根目录；显式指定的相对 `--output`
相对于调用时的当前目录。打包实现见 [binary_packager.py](../tools/package/binary_packager.py)，
完整用法见 [打包说明](../tools/package/BINARY_PACKAGING.md)。

## 新增或迁移 gen_*.py

1. 把入口放在 `apps/gen_<name>.py`，第一行使用 `#!/usr/bin/env python3`。
2. 先导入 `_cli`，再导入 `modules.simulation.tools` 下的业务模块。
3. 使用下面的流程组织入口，业务函数只接收明确的参数。
4. 执行 `chmod +x modules/simulation/apps/gen_<name>.py`，加载环境后直接调用。

入口示例（将 `task_generator` 替换为实际业务模块）：

```python
#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys

from _cli import WORKSPACE_ROOT, run_cli
from modules.simulation.tools.task_generator import generate


def make_parser():
    parser = argparse.ArgumentParser(description="Generate a simulation task")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=WORKSPACE_ROOT / "data/tasks")
    return parser


def validate_args(parser, args):
    if not args.input.is_file():
        parser.error(f"Input does not exist: {args.input}")


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    generate(input_path=args.input, output_path=args.output)
    return 0


if __name__ == "__main__":
    sys.exit(run_cli(main))
```

新的 `gen_simulation_task.py` 使用 `tools/execution` 的 Binary 和任务协议：

```bash
# source 已解压 binary 的 setup.bash 后，可以直接调用包内入口。
set -o pipefail
gen_simulation_task.py -B -1 -f /path/to/scene.json --repeat 2 | launch.py -c local
export BINARY_SERVICE_URL=http://127.0.0.1:8088
gen_simulation_task.py -B 123143 -f /path/to/scene.json --repeat 2 | launch.py -c local
```

本地选择、ID 服务、ML、集群扩展和验收见
[Binary 执行说明](../tools/execution/README.md)。入口遵循参数解析、业务校验、执行的流程，
stdout 用于机器可读 JSON，stderr 用于进度和错误。
原 `logsim/tools/gen_simulation_task.py` 保持原有 LogSim 生成接口，已有直接路径调用不变；
PATH 中的新命令使用此处的任务协议。

回归测试：

```bash
python3 -m unittest discover -s modules/simulation/apps/tests -v
python3 -m unittest discover -s modules/simulation/tools/package -p test_gen_binary.py -v
```

已通过 3 项 CLI 集成测试和 27 项打包回归测试。从 `/tmp` 直接执行新入口的
真实编译打包也已通过：Cyber Recorder 运行包 29.9 MiB、104 个 ELF，解压并
source 包内环境后运行成功。具体记录见 [CLI 验证结果](../tools/package/VALIDATION.md)。
