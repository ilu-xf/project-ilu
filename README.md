# project-ilu

根据一组 `hierarchy + cell pin` 数据生成 Verilog module 文件。

## 功能

新增脚本：`tools/generate_verilog_modules.py`

- 输入支持：
  - **hierarchy 结构 JSON**
  - **扁平 records JSON**
  - **扁平 records CSV**
- 输出：每个 hierarchy 一个 `.v` 文件
- 生成内容包含：
  - module 端口声明（如果输入里提供了端口信息）
  - cell 例化
  - pin 到 net 的连接（`.PIN(net)`）
  - 自动推断并声明内部 `wire`

## 输入格式示例

### 1) hierarchy 结构 JSON

见：`examples/hierarchy_data.json`

```json
{
  "hierarchies": [
    {
      "name": "top",
      "pins": [
        {"name": "clk", "direction": "input", "width": 1},
        {"name": "rst_n", "direction": "input", "width": 1},
        {"name": "y", "direction": "output", "width": 1}
      ],
      "cells": [
        {"name": "u_and", "type": "AND2X1", "pins": {"A": "clk", "B": "rst_n", "Y": "n1"}},
        {"name": "u_buf", "type": "BUF1", "pins": {"A": "n1", "Y": "y"}}
      ]
    }
  ]
}
```

### 2) 扁平 records CSV

见：`examples/flat_records.csv`

必须至少包含：
- `hierarchy`（或 `module`）
- `instance`
- `cell_type`
- `pin`
- `net`

若要描述 module 端口，可额外使用一行标记 `module_pin=true`，并带 `direction` / `width`。

## 使用方法

```bash
python3 tools/generate_verilog_modules.py \
  --input examples/hierarchy_data.json \
  --output-dir out_verilog \
  --manifest out_verilog/manifest.json
```

执行后将生成：
- `out_verilog/<hierarchy_name>.v`
- `out_verilog/manifest.json`（可选）

## 输出示例（片段）

```verilog
module top (
    clk,
    rst_n,
    y
);

  input clk;
  input rst_n;
  output y;

  wire n1;

  AND2X1 u_and (
    .A(clk),
    .B(rst_n),
    .Y(n1)
  );

  BUF1 u_buf (
    .A(n1),
    .Y(y)
  );
endmodule
```
