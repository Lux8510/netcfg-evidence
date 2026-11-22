# 跨环境设备映射

第一阶段用「设备名 == 包内节点」绑定目标设备，无法表达“两个环境都有 r2”。本阶段改为显式映射：

```
(environment, topology, node)  ->  device_id
```

映射由登录用户登记，记录 `created_by` / `created_at`；同一三元组只能有一条有效绑定。

## API

```http
POST   /api/mappings
GET    /api/mappings?environment=&topology=&device_id=
DELETE /api/mappings/{id}          # 停用，保留历史
```

示例：

```bash
curl -X POST /api/mappings -H 'Content-Type: application/json' -d '{
  "environment": "prod",
  "topology": "core",
  "node": "r2",
  "device_id": "<device-uuid>"
}'
```

冲突返回 `MAPPING_EXISTS`；停用后同一三元组可重新登记。

## 导入

`POST /api/devices/{id}/diagnosis-imports` 新增必填字段：

```json
{"bundle": {...}, "environment": "prod", "topology": "core"}
```

服务端在写入前：

1. 按 `(environment, topology, 包内节点)` 查有效映射；
2. 映射不存在 → `MAPPING_NOT_FOUND`；
3. 映射指向其他设备 → `MAPPING_DEVICE_MISMATCH`；
4. 两条都通过才导入。

Area 不一致场景的 R005 草稿也按同一环境/拓扑解析对端节点；对端没有映射或没有登记链路时只存证，原因写入 `no_draft_reason`。

CLI：

```bash
python scripts/import_diagnosis.py --bundle handoff.json --username 何深 \
  --device-id <uuid> --environment prod --topology core
```

## 兼容策略

- **SQLite / PostgreSQL 现有数据**：迁移只新增 `device_mappings` 表和 `diagnosis_imports.environment/topology` 两个可空列；旧导入记录保持 `NULL`，不重写、不推断。
- **schema v3 证据包**：包格式不变；环境/拓扑是请求上下文，不是包字段。旧包可直接用于新流程，只要导入时提供映射。
- **旧设备**：没有映射就不会被新导入命中；需要显式登记。**不根据设备名自动推断**跨环境映射。
- **旧导入**：仍可在设备详情查看；没有环境信息的历史记录不会参与新的映射解析。
- 失败回滚、幂等（bundle_sha256 / trace_id）与历史版本策略保持不变。
- **去重按上下文隔离**：同一设备可以在不同环境/拓扑下各有一条导入记录；`duplicate=true` 只会在相同 `(device, environment, topology, bundle/trace)` 下出现。跨上下文会新建规则运行与草稿，不复用旧记录。
- 旧记录 `environment/topology` 为 `NULL`，新导入不会匹配或复用这些记录，仍可查询。

## 前端

设备详情「诊断导入」卡片新增环境、拓扑输入；上传时与实际选择一起提交。结果仍区分：已生成草稿、仅存证、重复导入、服务端拒绝原因。
